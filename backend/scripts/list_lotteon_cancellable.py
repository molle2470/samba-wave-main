"""LOTTEON 발주완료 + 배송전 주문 추출."""

import asyncio

import asyncpg


async def main() -> None:
    from backend.core.config import settings

    conn = await asyncpg.connect(
        host=settings.write_db_host, port=settings.write_db_port,
        user=settings.write_db_user, password=settings.write_db_password,
        database=settings.write_db_name, ssl=False,
    )
    try:
        rows = await conn.fetch(
            """
            SELECT order_number, sourcing_order_number, sourcing_account_id,
                   status, shipping_status, channel_name, paid_at
            FROM samba_order
            WHERE source_site = 'LOTTEON'
              AND sourcing_order_number IS NOT NULL
              AND sourcing_order_number <> ''
              AND COALESCE(status, '') NOT IN (
                'cancel_requested','cancelling','cancelled',
                'return_requested','returning','returned','return_completed',
                'exchange_requested','exchanging','exchanged'
              )
              AND COALESCE(shipping_status, '') NOT LIKE '%배송중%'
              AND COALESCE(shipping_status, '') NOT LIKE '%배송완료%'
              AND COALESCE(shipping_status, '') NOT LIKE '%구매확정%'
              AND COALESCE(shipping_status, '') NOT LIKE '%송장전송완료%'
              AND COALESCE(shipping_status, '') NOT LIKE '%취소%'
            ORDER BY paid_at DESC NULLS LAST
            LIMIT 10
            """
        )
        for r in rows:
            print(
                f"sord={r['sourcing_order_number']:<20} "
                f"acct={(r['sourcing_account_id'] or '')[:14]:<16} "
                f"status={(r['status'] or '')[:14]:<16} "
                f"ship={(r['shipping_status'] or '')[:10]:<12} "
                f"paid={r['paid_at']}"
            )
        print(f"\nTotal: {len(rows)}")
    finally:
        await conn.close()


asyncio.run(main())
