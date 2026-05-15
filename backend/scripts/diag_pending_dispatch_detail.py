"""13건 대상 주문의 송장 출처 확인 — tracking_sync 잡 있는지/언제 입력됐는지."""

import asyncio

import asyncpg


async def main() -> None:
    from backend.core.config import settings

    conn = await asyncpg.connect(
        host=settings.write_db_host,
        port=settings.write_db_port,
        user=settings.write_db_user,
        password=settings.write_db_password,
        database=settings.write_db_name,
        ssl=False,
    )
    try:
        rows = await conn.fetch(
            """
            SELECT a.market_type,
                   o.id AS order_id,
                   o.order_number,
                   o.customer_name,
                   o.shipping_company,
                   o.tracking_number,
                   o.shipping_status,
                   o.shipped_at,
                   j.id AS job_id,
                   j.status AS job_status,
                   j.scraped_at,
                   j.dispatched_to_market_at,
                   j.last_error AS job_last_error
            FROM samba_order o
            JOIN samba_market_account a ON a.id = o.channel_id
            LEFT JOIN LATERAL (
                SELECT id, status, scraped_at, dispatched_to_market_at, last_error
                FROM samba_tracking_sync_job
                WHERE order_id = o.id
                ORDER BY updated_at DESC
                LIMIT 1
            ) j ON true
            WHERE o.tracking_number IS NOT NULL
              AND o.tracking_number <> ''
              AND a.market_type <> 'playauto'
              AND (
                o.shipping_status IS NULL
                OR o.shipping_status = ''
                OR o.shipping_status NOT IN ('송장전송완료', '배송중', '배송완료')
              )
            ORDER BY a.market_type, o.order_number
            """
        )
        for r in rows:
            print(
                f"[{r['market_type']:10s}] {r['order_number']:25s} "
                f"송장={r['shipping_company']}/{r['tracking_number']} "
                f"shipstatus={r['shipping_status']!r} "
                f"job={r['job_status']} scraped={r['scraped_at']!s:20s} "
                f"dispatched={r['dispatched_to_market_at']!s} "
                f"err={r['job_last_error']!r}"
            )
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
