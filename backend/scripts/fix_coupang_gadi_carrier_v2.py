"""가디 계정 쿠팡 DENIED 상품 삭제 후 롯데택배(HYUNDAI)로 재등록.

삼바 DB market_product_nos 업데이트로 상품 연결 유지.
registered_accounts 는 계정 ID가 변하지 않으므로 그대로 유지됨.
"""

import asyncio
import json
import asyncpg
from backend.core.config import settings

ACCOUNT_ID = "ma_01KNZV0ZWXW52W0G4TYG3AJH9Q"
LOTTE_CODE = "HYUNDAI"


async def get_db_conn():
    return await asyncpg.connect(
        host=settings.write_db_host,
        port=settings.write_db_port,
        user=settings.write_db_user,
        password=settings.write_db_password,
        database=settings.write_db_name,
        ssl="require" if settings.use_db_ssl else None,
    )


async def main() -> None:
    conn = await get_db_conn()

    # 가디 계정 인증정보 조회
    row = await conn.fetchrow(
        "SELECT account_label, additional_fields FROM samba_market_account WHERE id = $1",
        ACCOUNT_ID,
    )
    await conn.close()

    if not row:
        print(f"계정 {ACCOUNT_ID} 없음.")
        return

    label = row["account_label"]
    raw = row["additional_fields"] or {}
    extras = json.loads(raw) if isinstance(raw, str) else (raw or {})

    access_key = extras.get("accessKey") or ""
    secret_key = extras.get("secretKey") or ""
    vendor_id = extras.get("vendorId") or ""

    if not access_key or not secret_key or not vendor_id:
        print("인증정보 누락 — accessKey/secretKey/vendorId 확인 필요")
        return

    print(f"▶ 계정: {label} ({ACCOUNT_ID})")
    await process(access_key, secret_key, vendor_id)


async def process(access_key: str, secret_key: str, vendor_id: str) -> None:
    from backend.domain.samba.proxy.coupang import CoupangClient

    client = CoupangClient(access_key, secret_key, vendor_id)

    print("  DENIED 상품 목록 조회 중...")
    denied = await client.list_seller_products(status="DENIED")
    print(f"  DENIED 상품 수: {len(denied):,}개")

    if not denied:
        print("  처리할 상품 없음.")
        return

    ok = 0
    fail = 0
    skip = 0
    fail_log = []

    for item in denied:
        spid = item["seller_product_id"]
        name = item["product_name"][:50]

        try:
            # 1. 현재 상품 데이터 GET
            resp = await client.get_product(spid)
            if not isinstance(resp, dict):
                raise ValueError(f"get_product 응답 오류: {resp}")

            # 응답 구조: { data: { ...상품데이터... } } 또는 그냥 상품데이터
            data = resp.get("data", resp) if isinstance(resp, dict) else resp
            if not isinstance(data, dict):
                raise ValueError(f"data 필드 없음: {resp}")

            # 2. deliveryCompanyCode 확인
            current_code = data.get("deliveryCompanyCode", "")
            if current_code == LOTTE_CODE:
                skip += 1
                continue

            # 3. 택배사 변경
            data["deliveryCompanyCode"] = LOTTE_CODE

            # vendorId/vendorUserId 필수 필드 보장
            if not data.get("vendorId"):
                data["vendorId"] = vendor_id
            if not data.get("vendorUserId"):
                data["vendorUserId"] = vendor_id

            # 4. 기존 상품 삭제
            await client.delete_product(spid)
            await asyncio.sleep(0.3)

            # 5. 재등록
            reg_result = await client.register_product(data)
            new_spid = ""
            if isinstance(reg_result, dict):
                inner = reg_result.get("data", {})
                if isinstance(inner, dict):
                    new_spid = str(inner.get("data", "") or "")
                elif inner:
                    new_spid = str(inner)

            if not new_spid or not new_spid.isdigit():
                fail += 1
                fail_log.append(
                    f"삭제됨but재등록실패 spid={spid} ({name}): {reg_result}"
                )
                print(f"  ✗ {spid} → 삭제됐지만 재등록 실패! 수동 확인 필요")
                continue

            # 6. DB market_product_nos 업데이트
            await update_db(spid, new_spid)

            print(
                f"  ✓ {spid} → {new_spid} ({name[:30]}) [{current_code}→{LOTTE_CODE}]"
            )
            ok += 1
            await asyncio.sleep(0.5)

        except Exception as e:
            fail += 1
            fail_log.append(f"spid={spid} ({name}): {e}")
            print(f"  ✗ {spid} ({name[:30]}) 오류: {e}")

    print("\n  ═══════════════════════════════")
    print(f"  완료: 성공 {ok:,}개 / 실패 {fail:,}개 / skip(이미 롯데) {skip:,}개")
    if fail_log:
        print("\n  실패 목록:")
        for f in fail_log:
            print(f"    - {f}")


async def update_db(old_spid: str, new_spid: str) -> None:
    """market_product_nos[ACCOUNT_ID] = new_spid 로 업데이트."""
    conn = await get_db_conn()
    try:
        # old_spid 로 상품 조회
        rows = await conn.fetch(
            """
            SELECT id
            FROM samba_collected_product
            WHERE market_product_nos ->> $1 = $2
            """,
            ACCOUNT_ID,
            old_spid,
        )
        if not rows:
            # 삼바 DB에 매핑 없음 — registered_accounts 만 있는 케이스 무시
            return

        for row in rows:
            product_id = row["id"]
            await conn.execute(
                """
                UPDATE samba_collected_product
                SET market_product_nos = jsonb_set(
                    COALESCE(market_product_nos, '{}'),
                    ARRAY[$1],
                    to_jsonb(CAST($2 AS text))
                )
                WHERE id = $3
                """,
                ACCOUNT_ID,
                new_spid,
                product_id,
            )
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
