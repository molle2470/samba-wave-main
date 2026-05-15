"""order.source_site 가 비어있는 주문에 대해 collected_product / source_url 로 채우는 백필.

근본 원인: _can_override_source_site_from_sourcing 가 PlayAuto 주문은 무조건 False
반환해서 매칭 성공해도 source_site 가 빈 채로 남음 (refactor 후 발생한 부작용).

채움 우선순위:
  1) collected_product.source_site (가장 정확 — 매칭된 상품의 진짜 소싱처)
  2) source_url 도메인 추론 (musinsa.com / lotteon.com / ...)

idempotent — 여러 번 실행 안전.
"""

import asyncio

import asyncpg


SITE_BY_DOMAIN = [
    ("musinsa.com", "MUSINSA"),
    ("kream.co.kr", "KREAM"),
    ("fashionplus.co.kr", "FashionPlus"),
    ("grandstage.a-rt.com", "GRANDSTAGE"),
    ("abcmart.a-rt.com", "ABCmart"),
    ("nike.com", "Nike"),
    ("ssg.com", "SSG"),
    ("lotteon.com", "LOTTEON"),
    ("gsshop.com", "GSSHOP"),
    ("oliveyoung.co.kr", "OLIVEYOUNG"),
]


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
        # 1) collected_product 매칭된 주문 → cp.source_site 로 채움
        n1 = await conn.execute(
            """
            UPDATE samba_order o
            SET source_site = cp.source_site
            FROM samba_collected_product cp
            WHERE o.collected_product_id = cp.id
              AND (o.source_site IS NULL OR o.source_site = '')
              AND cp.source_site IS NOT NULL
              AND cp.source_site <> ''
            """
        )
        print(f"[backfill] collected_product 매칭으로 채움: {n1}")

        # 2) source_url 도메인 추론
        for domain, site_code in SITE_BY_DOMAIN:
            n = await conn.execute(
                """
                UPDATE samba_order
                SET source_site = $1
                WHERE (source_site IS NULL OR source_site = '')
                  AND source_url ILIKE $2
                """,
                site_code,
                f"%{domain}%",
            )
            print(f"[backfill] source_url ~ {domain} → {site_code}: {n}")

        # 3) 잔존 빈 source_site 확인
        remain = await conn.fetchval(
            "SELECT COUNT(*) FROM samba_order WHERE (source_site IS NULL OR source_site = '')"
        )
        print(f"\n[backfill] 잔존 빈 source_site: {remain}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
