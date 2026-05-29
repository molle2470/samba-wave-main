"""쿠팡 옵션 노출상품ID로 style_code/market_product_nos 확인."""

import asyncio
import json

import asyncpg

from backend.core.config import settings


SELLER_PRODUCT_ID = "9542810128"  # 사용자 화면 노출상품ID


async def main() -> None:
    conn = await asyncpg.connect(
        host="172.18.0.2",
        port=5432,
        user=settings.write_db_user,
        password=settings.write_db_password,
        database=settings.write_db_name,
        ssl=False,
    )

    try:
        # 1) market_product_nos JSONB 값 안에 seller_product_id 가 들어있는 row 찾기
        row = await conn.fetchrow(
            """
            SELECT
              id,
              name,
              source_site,
              site_product_id,
              brand,
              style_code,
              status,
              registered_accounts,
              market_product_nos,
              created_at,
              updated_at
            FROM samba_collected_product
            WHERE market_product_nos::text LIKE $1
            LIMIT 1
            """,
            f"%{SELLER_PRODUCT_ID}%",
        )

        if not row:
            print(f"[NOT FOUND] seller_product_id={SELLER_PRODUCT_ID} 매칭 row 없음")
            return

        print("=" * 80)
        print(f"id              : {row['id']}")
        print(f"name            : {row['name']}")
        print(f"source_site     : {row['source_site']}")
        print(f"site_product_id : {row['site_product_id']}")
        print(f"brand           : {row['brand']}")
        print(f"style_code      : {row['style_code']!r}  <-- modelNo 후보")
        print(f"status          : {row['status']}")
        print(f"created_at      : {row['created_at']}")
        print(f"updated_at      : {row['updated_at']}")
        print()
        print("registered_accounts:")
        print(json.dumps(row["registered_accounts"], ensure_ascii=False, indent=2))
        print()
        print("market_product_nos:")
        mpn = row["market_product_nos"]
        if isinstance(mpn, str):
            mpn = json.loads(mpn)
        print(json.dumps(mpn, ensure_ascii=False, indent=2))
        print("=" * 80)

        # style_code 진단
        sc = (row["style_code"] or "").strip()
        if not sc:
            print("\n[진단] style_code 빈 값 → modelNo 박을 값 자체 없음.")
            print("       해결: 1) 소싱처 수집 시 style_code 채우기")
            print("             2) 또는 폴백 site_product_id 사용 (코드 변경 필요)")
        else:
            print(f"\n[진단] style_code='{sc}' 존재. 코드는 modelNo 전송함.")
            print("       의심: 등록 시점이 modelNo 코드 추가(2026-05-27) 전이면")
            print("             재전송(update) 한 번 필요. 또는 쿠팡 UI 캐시.")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
