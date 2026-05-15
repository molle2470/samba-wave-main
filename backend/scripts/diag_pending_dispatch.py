"""송장 입력은 됐지만 마켓에 전송 안된 주문 진단 (PlayAuto 제외)."""

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
            SELECT a.market_type, COUNT(*) AS cnt
            FROM samba_order o
            JOIN samba_market_account a ON a.id = o.channel_id
            WHERE o.tracking_number IS NOT NULL
              AND o.tracking_number <> ''
              AND a.market_type <> 'playauto'
              AND (
                o.shipping_status IS NULL
                OR o.shipping_status = ''
                OR o.shipping_status NOT IN ('송장전송완료', '배송중', '배송완료')
              )
            GROUP BY a.market_type
            ORDER BY cnt DESC
            """
        )
        print("=== 마켓 전송 대상 (PlayAuto 제외, tracking 있음, 마켓 전송 미완료) ===")
        total = 0
        for r in rows:
            print(f"  {r['market_type']:20s} {r['cnt']:5d}")
            total += r["cnt"]
        print(f"\n  합계: {total}건")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
