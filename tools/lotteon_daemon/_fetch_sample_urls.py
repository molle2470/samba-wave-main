# 프로덕션 DB에서 SSG/LOTTEON 샘플 상품 URL 추출 — CSS 차단 테스트용.
import asyncio

import asyncpg

from backend.core.config import settings


async def main():
    # config 의 write DB 인증정보 사용
    conn = await asyncpg.connect(
        host="172.18.0.2",
        port=5432,
        user=settings.write_db_user,
        password=settings.write_db_password,
        database=settings.write_db_name,
        ssl=False,
    )
    for site in ("SSG", "LOTTEON"):
        rows = await conn.fetch(
            """
            SELECT site_product_id, source_url, name
            FROM samba_collected_product
            WHERE source_site = $1 AND source_url IS NOT NULL AND source_url <> ''
            ORDER BY last_refreshed_at DESC NULLS LAST
            LIMIT 3
            """,
            site,
        )
        print(f"=== {site} ({len(rows)}건) ===")
        for r in rows:
            print(f"  id={r['site_product_id']} url={r['source_url']}")
    await conn.close()


asyncio.run(main())
