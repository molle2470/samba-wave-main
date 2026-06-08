"""스마트스토어 유령상품 일일 자동 진단·정리.

매일 1회 모든 활성 스마트스토어 계정에 대해:
1. 기존 `/smartstore/cleanup-orphans` 엔드포인트 로직을 그대로 재사용
   (Naver 등록상품 전체 조회 → DB market_product_nos/style_code diff → 고아 판별)
2. 고아 발견 시 samba_monitor_event(smartstore_ghost_detected) 기록 → 상품관리 배너 노출
3. SMARTSTORE_AUTO_CLEAN_GHOSTS=1 환경변수 켜져 있으면 자동 삭제(delete_product) (기본은 알림만)

엔드포인트 함수를 직접 호출해 삭제/stale 정리의 안전로직(삼바 등록분만 대상,
sellerManagementCode=style_code 매칭 보호, 레이트리밋/재시도)을 100% 공유한다.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from backend.db.orm import get_write_session
from backend.shutdown_state import is_shutting_down


logger = logging.getLogger("backend.smartstore.ghost_reconciler")

# 운영 파라미터
RUN_INTERVAL_SECONDS = 24 * 3600  # 하루 1회
INITIAL_DELAY_SECONDS = 60 * 30  # 부팅 직후 30분 뒤 첫 실행
ALERT_THRESHOLD = 50  # 고아 N개 초과 시 강한 알림
MAX_DELETE_PER_RUN = 1000  # 한 사이클당 전 계정 합산 최대 삭제 수
AUTO_CLEAN = os.environ.get("SMARTSTORE_AUTO_CLEAN_GHOSTS", "").lower() in (
    "1",
    "true",
    "yes",
)


async def _log_monitor_event(
    account_id: str, account_label: str, orphans: int, naver_total: int
) -> None:
    """samba_monitor_event 테이블에 알림 기록. 실패 시 silently skip."""
    try:
        from backend.domain.samba.warroom.model import SambaMonitorEvent

        async with get_write_session() as session:
            session.add(
                SambaMonitorEvent(
                    event_type="smartstore_ghost_detected",
                    severity="warning" if orphans < ALERT_THRESHOLD else "critical",
                    market_type="smartstore",
                    summary=f"스마트스토어 {account_label} 유령상품 {orphans}개 감지",
                    detail={
                        "account_id": account_id,
                        "account_label": account_label,
                        "ghosts": orphans,
                        "naver_total": naver_total,
                        "auto_clean_enabled": AUTO_CLEAN,
                    },
                )
            )
            await session.commit()
    except Exception as e:
        logger.debug(f"[smartstore_ghost_reconciler] monitor_event 기록 스킵: {e}")


async def reconcile_all_accounts_once() -> dict[str, Any]:
    """1회 실행 — 수동 트리거/테스트용. 기존 cleanup 엔드포인트 로직 재사용."""
    # 순환 import 회피 — 함수 내부 지연 import
    from fastapi import HTTPException

    from backend.api.v1.routers.samba.shipment import (  # noqa: F401
        CleanupOrphansRequest,
        cleanup_smartstore_orphans,
    )

    async with get_write_session() as session:
        try:
            result = await cleanup_smartstore_orphans(
                body=CleanupOrphansRequest(),
                dry_run=not AUTO_CLEAN,  # AUTO on → 실제 삭제, off → 감지만
                account_id=None,
                max_delete=MAX_DELETE_PER_RUN,
                session=session,
                admin="auto-reconciler",
            )
        except HTTPException as e:
            # 활성 계정 없음(404) 등 → 조용히 종료
            logger.info(f"[smartstore_ghost_reconciler] skip: {e.detail}")
            return {"accounts": []}
        except Exception as e:
            logger.exception(f"[smartstore_ghost_reconciler] cleanup 실패: {e}")
            return {"error": str(e), "accounts": []}

    # 계정별 고아 발견 시 monitor_event 기록 (배너용) — 별도 세션
    for acc in result.get("accounts") or []:
        orphan_count = int(acc.get("orphan_count") or 0)
        if orphan_count <= 0:
            continue
        severity = "WARN" if orphan_count < ALERT_THRESHOLD else "CRIT"
        logger.warning(
            f"[smartstore_ghost_reconciler] {severity} {acc.get('account_id')} "
            f"유령={orphan_count} naver={acc.get('naver_count')} "
            f"deleted={len(acc.get('deleted') or [])}"
        )
        await _log_monitor_event(
            str(acc.get("account_id") or ""),
            str(acc.get("account_id") or ""),
            orphan_count,
            int(acc.get("naver_count") or 0),
        )

    logger.info(
        f"[smartstore_ghost_reconciler] 완료 auto_clean={AUTO_CLEAN} "
        f"total_orphans={result.get('total_orphans')} "
        f"total_deleted={result.get('total_deleted')}"
    )
    return result


async def ghost_reconciler_loop() -> None:
    """24시간 주기 백그라운드 루프 — lifecycle에서 create_task 로 기동."""
    logger.info(
        f"[smartstore_ghost_reconciler] 시작 — interval=24h, auto_clean={AUTO_CLEAN}, "
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
                f"[smartstore_ghost_reconciler] cycle 실패(다음 cycle에 재시도): {e}"
            )
        # 다음 사이클까지 대기 (취소 신호 빠르게 받기 위해 분할 sleep)
        slept = 0
        while slept < RUN_INTERVAL_SECONDS and not is_shutting_down():
            await asyncio.sleep(min(60, RUN_INTERVAL_SECONDS - slept))
            slept += 60
