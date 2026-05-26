"""dry-run — UPDATE 영향 행 수만 카운트."""

import asyncio

import asyncpg

from backend.core.config import settings


async def main():
    conn = await asyncpg.connect(
        host="172.18.0.2",
        port=5432,
        user=settings.write_db_user,
        password=settings.write_db_password,
        database=settings.write_db_name,
        ssl=False,
    )
    try:
        total_empty = await conn.fetchval(
            "SELECT COUNT(*) FROM samba_order WHERE product_image IS NULL OR product_image = ''"
        )
        print(f"product_image 비어있는 주문 총: {total_empty:,}건")

        n1 = await conn.fetchval(
            """
            SELECT COUNT(*) FROM samba_order o
            JOIN samba_collected_product cp ON cp.id = o.collected_product_id
            WHERE (o.product_image IS NULL OR o.product_image = '')
              AND cp.images IS NOT NULL
              AND json_typeof(cp.images) = 'array'
              AND json_array_length(cp.images) > 0
            """
        )
        print(f"[1] collected_product_id 매칭 가능: {n1:,}건")

        n2 = await conn.fetchval(
            """
            SELECT COUNT(*) FROM samba_order o
            JOIN samba_collected_product cp ON cp.source_url = o.source_url
            WHERE o.collected_product_id IS NULL
              AND o.source_url IS NOT NULL
              AND o.source_url <> ''
              AND (o.product_image IS NULL OR o.product_image = '')
              AND cp.images IS NOT NULL
              AND json_typeof(cp.images) = 'array'
              AND json_array_length(cp.images) > 0
            """
        )
        print(f"[2] source_url 매칭 가능 (cpid 없음): {n2:,}건")
        print(
            f"\n총 백필 대상: {n1 + n2:,}건 / 잔여 미매칭: {total_empty - n1 - n2:,}건"
        )
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
