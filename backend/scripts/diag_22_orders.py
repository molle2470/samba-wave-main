"""source_site 없는 22건 주문자 리스트."""

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
            WITH kst_range AS (
              SELECT
                ((date_trunc('day', now() AT TIME ZONE 'Asia/Seoul') - interval '6 days')
                  AT TIME ZONE 'Asia/Seoul') AS s,
                ((date_trunc('day', now() AT TIME ZONE 'Asia/Seoul') + interval '1 day')
                  AT TIME ZONE 'Asia/Seoul') AS e
            )
            SELECT
              o.order_number,
              o.shipment_id,
              o.customer_name,
              o.channel_name,
              o.product_name,
              o.action_tag,
              o.status,
              o.shipping_status,
              o.paid_at,
              o.sourcing_order_number
            FROM samba_order o, kst_range
            WHERE (o.tracking_number IS NULL OR o.tracking_number = '')
              AND COALESCE(o.paid_at, o.created_at) >= kst_range.s
              AND COALESCE(o.paid_at, o.created_at) < kst_range.e
              AND (o.status IS NULL OR o.status NOT IN (
                'cancel_requested','cancelling','cancelled',
                'return_requested','returning','returned','return_completed',
                'exchange_requested','exchanging','exchanged','exchange_pending','exchange_done',
                'ship_failed','undeliverable'
              ))
              AND (
                o.shipping_status IS NULL
                OR (
                  o.shipping_status NOT LIKE '%배송중%'
                  AND o.shipping_status NOT LIKE '%배송완료%'
                )
              )
              AND (',' || COALESCE(o.action_tag, '') || ',' NOT LIKE '%,kkadaegi,%')
              AND o.sourcing_order_number IS NOT NULL
              AND o.sourcing_order_number <> ''
              AND (o.source_site IS NULL OR o.source_site = '')
            ORDER BY COALESCE(o.paid_at, o.created_at) DESC
            """
        )
        print(f"=== source_site 없는 22건 — 큐잉 거부됨 ({len(rows)}건) ===\n")
        print(f"{'#':>3} {'결제일':>16} {'주문번호':25s} {'주문번호2':12s} {'고객명':10s} "
              f"{'마켓':30s} {'소싱주문번호':25s} {'액션':25s}")
        print("-" * 180)
        for i, r in enumerate(rows, 1):
            paid = r["paid_at"].strftime("%m/%d %H:%M") if r["paid_at"] else "-"
            print(
                f"{i:>3} {paid:>16} {r['order_number']:25s} {r['shipment_id'] or '-':12s} "
                f"{r['customer_name'] or '-':10s} {(r['channel_name'] or '-')[:30]:30s} "
                f"{r['sourcing_order_number']:25s} {r['action_tag'] or '-':25s}"
            )
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
