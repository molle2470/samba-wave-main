"""쿠팡 유령상품 일일 자동 진단·정리.

매일 1회 모든 활성 쿠팡 계정에 대해:
1. 기존 `/coupang/cleanup-orphans` 엔드포인트 로직을 그대로 재사용
   (list_seller_products 전체 조회 → DB sellerProductId diff → orphan/stale 분류)
2. 고아 발견 시 samba_monitor_event(coupang_ghost_detected) 기록 → 상품관리 배너 노출
3. COUPANG_AUTO_CLEAN_GHOSTS=1 환경변수 켜져 있으면 자동 삭제(delete_product) (기본은 알림만)

엔드포인트 함수를 직접 호출해 삭제/stale 정리의 안전로직(DELETED/DENIED 제외,
레이트리밋/재시도, stale DB 매핑 정리)을 100% 공유한다.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from backend.db.orm import get_write_session
from backend.shutdown_state import is_shutting_down


logger = logging.getLogger("backend.coupang.ghost_reconciler")

# 운영 파라미터
RUN_INTERVAL_SECONDS = 24 * 3600  # 하루 1회
INITIAL_DELAY_SECONDS = 60 * 30  # 부팅 직후 30분 뒤 첫 실행
ALERT_THRESHOLD = 50
MAX_DELETE_PER_RUN = 1000  # 한 사이클당 전 계정 합산 최대 삭제 수
AUTO_CLEAN = os.environ.get("COUPANG_AUTO_CLEAN_GHOSTS", "").lower() in (
    "1",
    "true",
    "yes",
)


async def _log_monitor_event(
    account_id: str, account_label: str, orphans: int, market_total: int
) -> None:
    """samba_monitor_event 테이블에 알림 기록. 실패 시 silently skip."""
    try:
        from backend.domain.samba.warroom.model import SambaMonitorEvent

        async with get_write_session() as session:
            session.add(
                SambaMonitorEvent(
                    event_type="coupang_ghost_detected",
                    severity="warning" if orphans < ALERT_THRESHOLD else "critical",
                    market_type="coupang",
                    summary=f"쿠팡 {account_label} 유령상품 {orphans}개 감지",
                    detail={
                        "account_id": account_id,
                        "account_label": account_label,
                        "ghosts": orphans,
                        "coupang_total": market_total,
                        "auto_clean_enabled": AUTO_CLEAN,
                    },
                )
            )
            await session.commit()
    except Exception as e:
        logger.debug(f"[coupang_ghost_reconciler] monitor_event 기록 스킵: {e}")


async def reconcile_all_accounts_once() -> dict[str, Any]:
    """1회 실행 — 수동 트리거/테스트용. 기존 cleanup 엔드포인트 로직 재사용."""
    # 순환 import 회피 — 함수 내부 지연 import
    from fastapi import HTTPException

    from backend.api.v1.routers.samba.shipment import (  # noqa: F401
        CleanupOrphansRequest,
        cleanup_coupang_orphans,
    )

    async with get_write_session() as session:
        try:
            result = await cleanup_coupang_orphans(
                body=CleanupOrphansRequest(),
                dry_run=not AUTO_CLEAN,  # AUTO on → 실제 삭제, off → 감지만
                account_id=None,
                max_delete=MAX_DELETE_PER_RUN,
                full=False,
                session=session,
                admin="auto-reconciler",
            )
        except HTTPException as e:
            logger.info(f"[coupang_ghost_reconciler] skip: {e.detail}")
            return {"accounts": []}
        except Exception as e:
            logger.exception(f"[coupang_ghost_reconciler] cleanup 실패: {e}")
            return {"error": str(e), "accounts": []}

    # 계정별 고아 발견 시 monitor_event 기록 (배너용) — 별도 세션
    for acc in result.get("accounts") or []:
        orphan_count = int(acc.get("orphan_count") or 0)
        if orphan_count <= 0:
            continue
        severity = "WARN" if orphan_count < ALERT_THRESHOLD else "CRIT"
        logger.warning(
            f"[coupang_ghost_reconciler] {severity} {acc.get('account_id')} "
            f"유령={orphan_count} market={acc.get('market_count')} "
            f"deleted={len(acc.get('deleted') or [])}"
        )
        await _log_monitor_event(
            str(acc.get("account_id") or ""),
            str(acc.get("label") or acc.get("account_id") or ""),
            orphan_count,
            int(acc.get("market_count") or 0),
        )

    logger.info(
        f"[coupang_ghost_reconciler] 완료 auto_clean={AUTO_CLEAN} "
        f"total_orphans={result.get('total_orphans')} "
        f"total_deleted={result.get('total_deleted')}"
    )
    return result


async def ghost_reconciler_loop() -> None:
    """24시간 주기 백그라운드 루프 — lifecycle에서 create_task 로 기동."""
    logger.info(
        f"[coupang_ghost_reconciler] 시작 — interval=24h, auto_clean={AUTO_CLEAN}, "
        f"first_run_in={INITIAL_DELAY_SECONDS}s"
    )
    await asyncio.sleep(INITIAL_DELAY_SECONDS)
    while not is_shutting_down():
        try:
            await reconcile_all_accounts_once()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception(
                f"[coupang_ghost_reconciler] cycle 실패(다음 cycle에 재시도): {e}"
            )
        slept = 0
        while slept < RUN_INTERVAL_SECONDS and not is_shutting_down():
            await asyncio.sleep(min(60, RUN_INTERVAL_SECONDS - slept))
            slept += 60
