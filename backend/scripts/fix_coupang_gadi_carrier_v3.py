"""가디 계정 쿠팡 DENIED 상품 삭제 후 롯데택배(HYUNDAI)로 재등록 v3.

수정사항:
- GET 응답에서 sellerProductId / vendorItemId 제거 후 POST (재등록 성공 핵심)
- 이미 삭제된 58개도 DELETED 상태에서 GET 재시도 후 재등록
- 삼바 DB market_product_nos 업데이트로 상품 연결 유지
"""

import asyncio
import json
import asyncpg
from backend.core.config import settings

ACCOUNT_ID = "ma_01KNZV0ZWXW52W0G4TYG3AJH9Q"
LOTTE_CODE = "HYUNDAI"

# v2에서 이미 삭제됐으나 재등록 실패한 58개 sellerProductId
ALREADY_DELETED = [
    "16229977484",
    "16229691684",
    "16229690659",
    "16229690249",
    "16229689733",
    "16229689337",
    "16229688892",
    "16229688454",
    "16229688139",
    "16229687939",
    "16229687630",
    "16229686788",
    "16229685973",
    "16229685264",
    "16229684640",
    "16229684196",
    "16229683777",
    "16229683405",
    "16229683047",
    "16229682583",
    "16229682072",
    "16229681729",
    "16229681541",
    "16229681386",
    "16229681214",
    "16229680936",
    "16229680657",
    "16229680310",
    "16229679860",
    "16229679424",
    "16229677956",
    "16229677620",
    "16229677197",
    "16229676720",
    "16229676313",
    "16229675856",
    "16229675526",
    "16229675122",
    "16229674772",
    "16229674515",
    "16229674137",
    "16229673606",
    "16229673301",
    "16229672848",
    "16229672385",
    "16229671860",
    "16229671621",
    "16229671474",
    "16229671198",
    "16229670825",
    "16229670577",
    "16229670304",
    "16229669954",
    "16229669668",
    "16229669305",
    "16229669046",
    "16229668733",
    "16229668418",
]


async def get_db_conn():
    return await asyncpg.connect(
        host=settings.write_db_host,
        port=settings.write_db_port,
        user=settings.write_db_user,
        password=settings.write_db_password,
        database=settings.write_db_name,
        ssl="require" if settings.use_db_ssl else None,
    )


def strip_server_ids(data: dict) -> dict:
    """재등록 시 서버 발급 ID 필드 제거."""
    data = dict(data)
    data.pop("sellerProductId", None)
    data.pop("productId", None)
    data.pop("approvalStatus", None)
    data.pop("statusName", None)
    data.pop("exposedStatusName", None)
    data.pop("createdAt", None)
    data.pop("updatedAt", None)
    # items 배열에서 vendorItemId 제거
    items = data.get("items")
    if isinstance(items, list):
        cleaned = []
        for item in items:
            if isinstance(item, dict):
                item = dict(item)
                item.pop("vendorItemId", None)
                item.pop("itemId", None)
            cleaned.append(item)
        data["items"] = cleaned
    return data


async def main() -> None:
    conn = await get_db_conn()
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
        print("인증정보 누락")
        return

    print(f"▶ 계정: {label} ({ACCOUNT_ID})")

    from backend.domain.samba.proxy.coupang import CoupangClient

    client = CoupangClient(access_key, secret_key, vendor_id)

    # ─── STEP 1: 이미 삭제된 58개 복구 ───────────────────────────────
    print(f"\n[STEP 1] 이미 삭제된 {len(ALREADY_DELETED)}개 복구 중...")
    step1_ok, step1_fail = 0, 0
    for spid in ALREADY_DELETED:
        result = await register_one(client, spid, vendor_id, deleted=True)
        if result:
            step1_ok += 1
        else:
            step1_fail += 1
        await asyncio.sleep(0.5)

    print(f"  → 복구: 성공 {step1_ok}개 / 실패 {step1_fail}개")

    # ─── STEP 2: 나머지 DENIED 상품 처리 ─────────────────────────────
    print("\n[STEP 2] DENIED 상품 목록 조회 중...")
    denied = await client.list_seller_products(status="DENIED")
    # 이미 처리된 58개 제외
    already_set = set(ALREADY_DELETED)
    remaining = [d for d in denied if d["seller_product_id"] not in already_set]
    print(f"  DENIED 전체: {len(denied):,}개 / 미처리: {len(remaining):,}개")

    step2_ok, step2_fail, step2_skip = 0, 0, 0
    fail_log = []
    for item in remaining:
        spid = item["seller_product_id"]
        result = await register_one(client, spid, vendor_id, deleted=False)
        if result is True:
            step2_ok += 1
        elif result is None:
            step2_skip += 1
        else:
            step2_fail += 1
            fail_log.append(spid)
        await asyncio.sleep(0.5)

    print("\n  ═══════════════════════════════════════")
    print(f"  STEP1 복구: {step1_ok}개 성공 / {step1_fail}개 실패")
    print(
        f"  STEP2 신규: {step2_ok}개 성공 / {step2_fail}개 실패 / {step2_skip}개 skip"
    )
    if fail_log:
        print("\n  STEP2 실패 sellerProductId 목록:")
        for f in fail_log:
            print(f"    {f}")


async def register_one(client, spid: str, vendor_id: str, deleted: bool):
    """단일 상품 GET → 수정 → 재등록 → DB 업데이트.

    Returns: True=성공, False=실패, None=skip(이미 롯데)
    """
    try:
        resp = await client.get_product(spid)
        if not isinstance(resp, dict):
            print(f"  ✗ {spid} — get_product 응답 오류")
            return False

        data = resp.get("data", resp) if isinstance(resp, dict) else resp
        if not isinstance(data, dict):
            print(f"  ✗ {spid} — data 필드 없음")
            return False

        current_code = data.get("deliveryCompanyCode", "")
        if not deleted and current_code == LOTTE_CODE:
            return None  # skip

        # 택배사 변경 + 서버 ID 제거
        data["deliveryCompanyCode"] = LOTTE_CODE
        data["vendorId"] = vendor_id
        data["vendorUserId"] = vendor_id
        post_data = strip_server_ids(data)

        # DENIED 상품은 DELETE 후 재등록, ALREADY_DELETED는 이미 삭제됨
        if not deleted:
            await client.delete_product(spid)
            await asyncio.sleep(0.3)

        # 재등록
        reg_result = await client.register_product(post_data)
        new_spid = ""
        if isinstance(reg_result, dict):
            inner = reg_result.get("data", {})
            if isinstance(inner, dict):
                new_spid = str(inner.get("data", "") or "")
            elif inner:
                new_spid = str(inner)

        if not new_spid or not new_spid.isdigit():
            print(f"  ✗ {spid} — 재등록 실패 (응답: {str(reg_result)[:150]})")
            return False

        # DB 업데이트
        await update_db(spid, new_spid)
        name = data.get("sellerProductName", "")[:35]
        print(f"  ✓ {spid} → {new_spid} ({name}) [{current_code}→{LOTTE_CODE}]")
        return True

    except Exception as e:
        print(f"  ✗ {spid} — 오류: {e}")
        return False


async def update_db(old_spid: str, new_spid: str) -> None:
    conn = await get_db_conn()
    try:
        rows = await conn.fetch(
            "SELECT id FROM samba_collected_product WHERE market_product_nos ->> $1 = $2",
            ACCOUNT_ID,
            old_spid,
        )
        for row in rows:
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
                row["id"],
            )
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
