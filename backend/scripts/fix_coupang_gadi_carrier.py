"""가디 계정 쿠팡 승인반려 상품 택배사 → 롯데택배(HYUNDAI) 일괄 수정."""

import asyncio
import json
import asyncpg
from backend.core.config import settings


LOTTE_CODE = "HYUNDAI"  # 쿠팡 Wing API 롯데택배 코드


async def main() -> None:
    conn = await asyncpg.connect(
        host=settings.write_db_host,
        port=settings.write_db_port,
        user=settings.write_db_user,
        password=settings.write_db_password,
        database=settings.write_db_name,
        ssl="require" if settings.use_db_ssl else None,
    )

    # 가디 계정 조회 (market_type=coupang, account_label 에 '가디' 포함)
    rows = await conn.fetch(
        """
        SELECT id, account_label, additional_fields
        FROM samba_market_account
        WHERE market_type = 'coupang'
          AND (account_label ILIKE '%가디%' OR account_label ILIKE '%gadi%')
          AND is_active = true
        """
    )
    await conn.close()

    if not rows:
        print("가디 계정을 찾을 수 없습니다. account_label 확인 필요.")
        return

    for row in rows:
        account_id = row["id"]
        label = row["account_label"]
        raw = row["additional_fields"] or {}
        extras = json.loads(raw) if isinstance(raw, str) else (raw or {})
        print(f"\n▶ 계정: {label} ({account_id})")

        access_key = extras.get("accessKey") or ""
        secret_key = extras.get("secretKey") or ""
        vendor_id = extras.get("vendorId") or ""

        if not access_key or not secret_key or not vendor_id:
            print(f"  ✗ 인증정보 누락 — accessKey/secretKey/vendorId 확인 필요")
            continue

        await fix_denied_products(access_key, secret_key, vendor_id, label)


async def fix_denied_products(
    access_key: str, secret_key: str, vendor_id: str, label: str
) -> None:
    from backend.domain.samba.proxy.coupang import CoupangClient

    client = CoupangClient(access_key, secret_key, vendor_id)

    print(f"  승인반려(DENIED) 상품 목록 조회 중...")
    denied = await client.list_seller_products(status="DENIED")
    print(f"  DENIED 상품 수: {len(denied)}개")

    if not denied:
        print("  처리할 상품 없음.")
        return

    ok = 0
    fail = 0
    for item in denied:
        spid = item["seller_product_id"]
        name = item["product_name"][:40]
        try:
            # 현재 상품 전체 데이터 조회
            data = await client.get_product(spid)
            if not isinstance(data, dict):
                print(f"  ✗ {spid} ({name}) — get_product 응답 오류")
                fail += 1
                continue

            # deliveryCompanyCode 변경
            current_code = data.get("deliveryCompanyCode", "")
            if current_code == LOTTE_CODE:
                print(f"  - {spid} ({name}) — 이미 롯데택배, skip")
                ok += 1
                continue

            data["deliveryCompanyCode"] = LOTTE_CODE

            # 수정 API 호출
            await client.update_product(spid, data)
            # 승인 재요청
            await client.approve_product(spid)
            print(f"  ✓ {spid} ({name}) — {current_code} → {LOTTE_CODE} 수정 + 승인요청")
            ok += 1
            await asyncio.sleep(0.5)  # rate limit 방지

        except Exception as e:
            print(f"  ✗ {spid} ({name}) — 오류: {e}")
            fail += 1

    print(f"\n  완료: 성공 {ok}개, 실패 {fail}개")


if __name__ == "__main__":
    asyncio.run(main())
