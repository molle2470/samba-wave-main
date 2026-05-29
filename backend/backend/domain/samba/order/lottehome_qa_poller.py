"""롯데홈쇼핑 QA 승인 상태 자동 동기화 — 30분 간격."""

import asyncio
import logging
import os

logger = logging.getLogger(__name__)

LOTTEHOME_QA_SYNC_INTERVAL = int(
    os.environ.get("LOTTEHOME_QA_SYNC_INTERVAL_SECONDS", str(30 * 60))
)


async def _run_lottehome_qa_sync() -> tuple[int, int]:
    """pending 상품을 조회하여 승인된 건을 approved로 업데이트. (checked, updated) 반환."""
    from sqlalchemy import text as _sa_text, update as sa_update
    from sqlmodel import select

    from backend.db.orm import get_write_session
    from backend.domain.samba.collector.model import SambaCollectedProduct
    from backend.domain.samba.forbidden.model import SambaSettings
    from backend.domain.samba.proxy.lottehome import LotteHomeClient

    # ── 1단계: credentials + pending 목록 조회 후 즉시 session 닫기 ──
    # HTTP 호출 전 DB 연결 반납 — idle in transaction 방지
    creds: dict = {}
    pending_items: list[tuple[str, str, str]] = []  # (product_id, acc_id, goods_no)

    async with get_write_session() as session:
        creds_result = await session.exec(
            select(SambaSettings).where(SambaSettings.key == "lottehome_credentials")
        )
        creds_row = creds_result.first()
        if not creds_row:
            return 0, 0
        creds = creds_row.value or {}

        user_id = creds.get("userId", "")
        password = creds.get("password", "")
        if not user_id or not password:
            return 0, 0

        # id + market_product_nos 만 조회 (heavy 컬럼 제외) + limit
        rows_result = await session.execute(
            _sa_text(
                "SELECT id, market_product_nos FROM samba_collected_product "
                "WHERE market_product_nos IS NOT NULL "
                "AND market_product_nos::text LIKE '%_qa%' "
                "LIMIT 2000"
            )
        )
        rows = rows_result.all()

        for row in rows:
            product_id, m_nos = row[0], row[1] or {}
            for k, v in m_nos.items():
                if k.endswith("_qa") and v == "pending":
                    acc_id = k.replace("_qa", "")
                    goods_no = m_nos.get(acc_id, "")
                    if goods_no:
                        pending_items.append((product_id, acc_id, goods_no))

        await session.commit()
    # ← session 종료 — DB 연결 반납 완료

    if not pending_items:
        return 0, 0

    # ── 2단계: HTTP 호출 (session 없이) ──
    lh_client = LotteHomeClient(
        user_id, password, creds.get("agncNo", ""), creds.get("env", "prod")
    )

    approved: list[tuple[str, str]] = []  # (product_id, acc_id)
    checked = 0

    for product_id, acc_id, goods_no in pending_items:
        checked += 1
        try:
            detail = await lh_client.search_goods_view(goods_no)
            data = detail.get("data", {})
            result_data = data.get("Result", data)
            goods_info = (
                result_data.get("GoodsInfo", result_data)
                if isinstance(result_data, dict)
                else result_data
            )
            if not isinstance(goods_info, dict):
                continue
            sale_stat = str(goods_info.get("SaleStatCd", "") or "")
            qa_result = str(goods_info.get("QaRsltCd", "") or "")
            if sale_stat == "10" or qa_result in ("10", "15", "30"):
                approved.append((product_id, acc_id))
        except Exception as exc:
            logger.warning("[롯데QA폴러] %s 체크 실패: %s", goods_no, exc)

    # ── 3단계: 승인된 건만 DB 업데이트 (건당 독립 session) ──
    updated = 0
    for product_id, acc_id in approved:
        try:
            async with get_write_session() as session:
                row_result = await session.execute(
                    _sa_text(
                        "SELECT market_product_nos FROM samba_collected_product "
                        "WHERE id = :pid"
                    ),
                    {"pid": product_id},
                )
                row = row_result.first()
                if not row:
                    continue
                m_nos = dict(row[0] or {})
                m_nos[f"{acc_id}_qa"] = "approved"
                await session.execute(
                    sa_update(SambaCollectedProduct)
                    .where(SambaCollectedProduct.id == product_id)
                    .values(market_product_nos=m_nos)
                )
                await session.commit()
                updated += 1
                logger.info("[롯데QA폴러] %s → approved (acc=%s)", product_id, acc_id)
        except Exception as exc:
            logger.warning("[롯데QA폴러] 업데이트 실패 %s: %s", product_id, exc)

    return checked, updated


async def start_lottehome_qa_poller() -> None:
    """롯데홈쇼핑 QA 승인 상태를 주기적으로 동기화하는 백그라운드 루프."""
    await asyncio.sleep(90)
    logger.info("[롯데QA폴러] 시작 (간격: %d초)", LOTTEHOME_QA_SYNC_INTERVAL)

    while True:
        try:
            checked, updated = await _run_lottehome_qa_sync()
            if checked:
                logger.info("[롯데QA폴러] 점검 %d건, 승인처리 %d건", checked, updated)
        except asyncio.CancelledError:
            logger.info("[롯데QA폴러] 종료")
            return
        except Exception as exc:
            logger.warning("[롯데QA폴러] 오류: %s", exc)

        await asyncio.sleep(LOTTEHOME_QA_SYNC_INTERVAL)
