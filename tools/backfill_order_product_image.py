"""주문 product_image 백필 — collected_product_id 기반 매칭.

eb3d5954에서 MPN 캐시 thumb=""로 저장 → 신규 매칭 주문 product_image 공란.
dry-run 결과: 22건 매칭 가능.
"""

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
        result = await conn.execute(
            """
            UPDATE samba_order o
            SET product_image = cp.images->>0
            FROM samba_collected_product cp
            WHERE o.collected_product_id = cp.id
              AND (o.product_image IS NULL OR o.product_image = '')
              AND cp.images IS NOT NULL
              AND json_typeof(cp.images) = 'array'
              AND json_array_length(cp.images) > 0
            """
        )
        print(f"백필 결과: {result}")

        remaining = await conn.fetchval(
            "SELECT COUNT(*) FROM samba_order WHERE product_image IS NULL OR product_image = ''"
        )
        print(f"잔여 product_image 공란: {remaining:,}건")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
