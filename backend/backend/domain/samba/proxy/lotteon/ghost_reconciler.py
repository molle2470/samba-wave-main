"""롯데ON 유령상품 일일 자동 진단·정리.

매일 새벽 1회 모든 활성 LOTTEON 계정에 대해:
1. list_registered_products(slStatCd=SALE) 전체 페이지 수집
2. 우리 DB market_product_nos 와 diff → 유령(LOTTEON-only) spdNo 도출
3. 임계치 초과 시 monitor_event 기록 + WARN 로그
4. LOTTEON_AUTO_END_GHOSTS=1 환경변수 켜져 있으면 자동 END(마켓삭제) (기본은 알림만)
   — 수동 '정리하기' 버튼(cleanup-orphans)과 동일하게 change_status(slStatCd=END) 처리
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from sqlalchemy import text

from backend.db.orm import get_write_session
from backend.domain.samba.proxy.lotteon import LotteonClient
from backend.shutdown_state import is_shutting_down


logger = logging.getLogger("backend.lotteon.ghost_reconciler")

# 운영 파라미터
RUN_INTERVAL_SECONDS = 24 * 3600  # 하루 1회
INITIAL_DELAY_SECONDS = 60 * 30  # 부팅 직후 30분 뒤 첫 실행
PAGE_SIZE = 100
ALERT_THRESHOLD = 50  # 유령 N개 초과 시 강한 알림
AUTO_END = os.environ.get("LOTTEON_AUTO_END_GHOSTS", "").lower() in (
    "1",
    "true",
    "yes",
)
END_BATCH = 50
END_BATCH_DELAY = 0.5


async def _fetch_active_lotteon_accounts() -> list[dict[str, Any]]:
    async with get_write_session() as session:
        rows = (
            (
                await session.execute(
                    text(
                        "SELECT id, account_label, seller_id, api_key, additional_fields "
                        "FROM samba_market_account "
                        "WHERE market_type='lotteon' AND is_active=true"
                    )
                )
            )
            .mappings()
            .all()
        )
    return [dict(r) for r in rows]


def _extract_api_key(acc: dict[str, Any]) -> str:
    api_key = (acc.get("api_key") or "").strip()
    if api_key:
        return api_key
    af = acc.get("additional_fields") or {}
    if isinstance(af, str):
        try:
            af = json.loads(af)
        except Exception:
            af = {}
    return (af or {}).get("apiKey", "") or ""


async def _fetch_db_known(account_id: str) -> set[str]:
    async with get_write_session() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT market_product_nos FROM samba_collected_product "
                    "WHERE market_product_nos IS NOT NULL AND market_product_nos ? :aid"
                ),
                {"aid": account_id},
            )
        ).all()
    result: set[str] = set()
    for (mpn,) in rows:
        if isinstance(mpn, str):
            try:
                mpn = json.loads(mpn)
            except Exception:
                mpn = {}
        if not isinstance(mpn, dict):
            continue
        v = mpn.get(account_id) or mpn.get(f"{account_id}_origin")
        if v is None:
            continue
        s = str(v).strip()
        if s:
            result.add(s)
    return result


async def _fetch_lotteon_sale(client: LotteonClient) -> set[str]:
    await client.test_auth()
    out: set[str] = set()
    page = 1
    while True:
        resp = await client.list_registered_products(
            page=page,
            size=PAGE_SIZE,
            reg_strt_dttm="20200101000000",
            reg_end_dttm="99991231235959",
            sl_stat_cd="SALE",
        )
        data = resp.get("data") or []
        if not isinstance(data, list):
            break
        for it in data:
            if isinstance(it, dict):
                spd = str(it.get("spdNo") or "").strip()
                if spd:
                    out.add(spd)
        if len(data) < PAGE_SIZE:
            break
        page += 1
        if page > 500:
            logger.warning("[ghost_reconciler] 500 페이지 초과 — 중단")
            break
    return out


async def _log_monitor_event(
    account_id: str, account_label: str, ghosts: int, lotteon_total: int
) -> None:
    """samba_monitor_event 테이블에 알림 기록. 실패 시 silently skip."""
    try:
        from backend.domain.samba.warroom.model import SambaMonitorEvent

        async with get_write_session() as session:
            session.add(
                SambaMonitorEvent(
                    event_type="lotteon_ghost_detected",
                    severity="warning" if ghosts < ALERT_THRESHOLD else "critical",
                    market_type="lotteon",
                    summary=f"롯데ON {account_label} 유령상품 {ghosts}개 감지",
                    detail={
                        "account_id": account_id,
                        "account_label": account_label,
                        "ghosts": ghosts,
                        "lotteon_sale_total": lotteon_total,
                        "auto_end_enabled": AUTO_END,
                    },
                )
            )
            await session.commit()
    except Exception as e:
        logger.debug(f"[ghost_reconciler] monitor_event 기록 스킵: {e}")


async def _auto_end(client: LotteonClient, ghosts: list[str]) -> tuple[int, int]:
    """AUTO_END=on 일 때만 호출. 유령 spdNo 를 END(마켓삭제) 처리. (success, failed) 반환."""
    ok = 0
    fail = 0
    for i in range(0, len(ghosts), END_BATCH):
        batch = ghosts[i : i + END_BATCH]
        payload = [{"spdNo": s, "slStatCd": "END"} for s in batch]
        try:
            res = await client.change_status(payload)
            data = (res or {}).get("data") or []
            if isinstance(data, list) and data:
                for item in data:
                    rc = (item or {}).get("resultCode", "")
                    if rc in ("", "0000", "00", "SUCCESS"):
                        ok += 1
                    else:
                        fail += 1
            else:
                ok += len(batch)
        except Exception as e:
            logger.warning(f"[ghost_reconciler] END 배치 실패: {e}")
            fail += len(batch)
        await asyncio.sleep(END_BATCH_DELAY)
    return ok, fail


async def _reconcile_one_account(acc: dict[str, Any]) -> dict[str, Any]:
    api_key = _extract_api_key(acc)
    if not api_key:
        return {"account_label": acc["account_label"], "skipped": "no api_key"}

    account_id = acc["id"]
    label = acc["account_label"]
    client = LotteonClient(api_key)
    try:
        db_known = await _fetch_db_known(account_id)
        lotteon_sale = await _fetch_lotteon_sale(client)
        ghosts = sorted(lotteon_sale - db_known)
        summary = {
            "account_label": label,
            "db_known": len(db_known),
            "lotteon_sale": len(lotteon_sale),
            "ghosts": len(ghosts),
        }

        if ghosts:
            severity = "WARN" if len(ghosts) < ALERT_THRESHOLD else "CRIT"
            logger.warning(
                f"[ghost_reconciler] {severity} {label} 유령={len(ghosts)} "
                f"db_known={len(db_known)} lotteon_sale={len(lotteon_sale)}"
            )
            await _log_monitor_event(account_id, label, len(ghosts), len(lotteon_sale))

            if AUTO_END:
                ok, fail = await _auto_end(client, ghosts)
                summary["auto_end_success"] = ok
                summary["auto_end_failed"] = fail
                logger.warning(
                    f"[ghost_reconciler] {label} AUTO_END 완료 success={ok} failed={fail}"
                )
        else:
            logger.info(
                f"[ghost_reconciler] OK {label} 유령없음 "
                f"db_known={len(db_known)} lotteon_sale={len(lotteon_sale)}"
            )
        return summary
    finally:
        await client.aclose()


async def reconcile_all_accounts_once() -> list[dict[str, Any]]:
    """1회 실행 — 수동 트리거/테스트용."""
    results: list[dict[str, Any]] = []
    accounts = await _fetch_active_lotteon_accounts()
    logger.info(f"[ghost_reconciler] 대상 LOTTEON 계정 {len(accounts)}개")
    for acc in accounts:
        try:
            r = await _reconcile_one_account(acc)
            results.append(r)
        except Exception as e:
            logger.exception(f"[ghost_reconciler] {acc.get('account_label')} 실패: {e}")
            results.append({"account_label": acc.get("account_label"), "error": str(e)})
    return results


async def ghost_reconciler_loop() -> None:
    """24시간 주기 백그라운드 루프 — lifecycle에서 create_task 로 기동."""
    logger.info(
        f"[ghost_reconciler] 시작 — interval=24h, auto_end={AUTO_END}, "
        f"first_run_in={INITIAL_DELAY_SECONDS}s"
    )
    await asyncio.sleep(INITIAL_DELAY_SECONDS)
    while not is_shutting_down():
        try:
            await reconcile_all_accounts_once()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception(f"[ghost_reconciler] cycle 실패(다음 cycle에 재시도): {e}")
        # 다음 사이클까지 대기 (취소 신호 빠르게 받기 위해 분할 sleep)
        slept = 0
        while slept < RUN_INTERVAL_SECONDS and not is_shutting_down():
            await asyncio.sleep(min(60, RUN_INTERVAL_SECONDS - slept))
            slept += 60
