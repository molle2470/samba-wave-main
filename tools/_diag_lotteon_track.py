"""특정 LOTTEON 주문 송장 잡 진단 (읽기 전용)."""

import asyncio

from sqlalchemy import text

from backend.db.orm import get_read_session

SRC_ORD = "2026060113922414"  # LOTTEON odNo
MKT_ORD = "2026060144957311"  # 스마트스토어 주문번호


async def main():
    async with get_read_session() as s:
        print("=== 송장 잡 (sourcing_order_number 매칭) ===")
        rows = (
            await s.execute(
                text(
                    """
                    SELECT id, order_id, sourcing_site, status, attempts,
                           scraped_courier, scraped_tracking, scraped_at,
                           last_error, created_at, updated_at
                    FROM samba_tracking_sync_job
                    WHERE sourcing_order_number = :src
                    ORDER BY created_at DESC
                    """
                ),
                {"src": SRC_ORD},
            )
        ).fetchall()
        for r in rows:
            print(
                f"  job={r[0]} order_id={r[1]} site={r[2]} status={r[3]} attempts={r[4]}\n"
                f"     courier={r[5]!r} track={r[6]!r} scraped_at={r[7]}\n"
                f"     last_error={r[8]!r}\n"
                f"     created={r[9]} updated={r[10]}"
            )
        if not rows:
            print("  (없음)")

        print("\n=== SambaOrder (마켓 주문번호 매칭) ===")
        orows = (
            await s.execute(
                text(
                    """
                    SELECT id, order_number, sourcing_order_number, source_site,
                           status, shipping_status, tracking_number, shipping_company,
                           notes, updated_at
                    FROM samba_order
                    WHERE order_number = :m OR sourcing_order_number = :src
                    ORDER BY updated_at DESC
                    """
                ),
                {"m": MKT_ORD, "src": SRC_ORD},
            )
        ).fetchall()
        for r in orows:
            print(
                f"  order={r[0]} num={r[1]} src_ord={r[2]} site={r[3]}\n"
                f"     status={r[4]!r} ship_status={r[5]!r} track={r[6]!r} comp={r[7]!r}\n"
                f"     notes={(r[8] or '')[:200]!r}\n"
                f"     updated={r[9]}"
            )
        if not orows:
            print("  (없음)")


asyncio.run(main())
