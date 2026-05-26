"""왜 9718건 매칭 안 되는지 진단."""

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
        rows = await conn.fetch(
            """
            SELECT
              COUNT(*) FILTER (WHERE collected_product_id IS NULL) AS no_cpid,
              COUNT(*) FILTER (WHERE collected_product_id IS NOT NULL) AS has_cpid,
              COUNT(*) FILTER (WHERE source_url IS NOT NULL AND source_url <> '') AS has_src_url,
              COUNT(*) FILTER (WHERE product_id IS NOT NULL AND product_id <> '') AS has_pid,
              COUNT(*) FILTER (WHERE source_site IS NOT NULL AND source_site <> '') AS has_site
            FROM samba_order
            WHERE product_image IS NULL OR product_image = ''
            """
        )
        for r in rows:
            print(dict(r))

        # 최근 7일 분포 (직접 이미지에 보인 주문)
        rows2 = await conn.fetch(
            """
            SELECT source_site, source, COUNT(*) AS n
            FROM samba_order
            WHERE (product_image IS NULL OR product_image = '')
              AND created_at >= NOW() - INTERVAL '7 days'
            GROUP BY source_site, source
            ORDER BY n DESC
            LIMIT 20
            """
        )
        print("\n최근 7일 빈 이미지 주문 분포:")
        for r in rows2:
            print(dict(r))

        # cpid 있는데 매칭 안 된 케이스 sample
        rows3 = await conn.fetch(
            """
            SELECT o.id, o.source_site, o.collected_product_id,
                   cp.images IS NULL AS cp_images_null,
                   json_typeof(cp.images) AS cp_images_type,
                   json_array_length(cp.images) AS cp_images_len
            FROM samba_order o
            LEFT JOIN samba_collected_product cp ON cp.id = o.collected_product_id
            WHERE (o.product_image IS NULL OR o.product_image = '')
              AND o.collected_product_id IS NOT NULL
            LIMIT 10
            """
        )
        print("\ncpid 있는데 미매칭 샘플:")
        for r in rows3:
            print(dict(r))
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
