"""가디 계정 쿠팡 DENIED/DELETED 상품 재등록 v4.

변경사항:
- sellerProductId/vendorItemId/productId 등 서버 ID 제거 (재등록 성공 핵심)
- 삼바 DB에서 style_code 조회 → items[*].modelNo 주입 (품번 의무화 대응)
- brand 있으면 search_brand_id로 brandId 조회 (브랜드ID 의무화 대응)
- emptyBarcode=True 처리 (바코드 없는 상품)
- 구매옵션은 기존 attributes 그대로 유지
- DELETED 상품(이미 삭제됨): DELETE 없이 바로 재등록
- DENIED 상품: DELETE 후 재등록
- 삼바 DB market_product_nos 업데이트로 상품 연결 유지
"""

import asyncio
import json
import asyncpg
from backend.core.config import settings

ACCOUNT_ID = "ma_01KNZV0ZWXW52W0G4TYG3AJH9Q"
LOTTE_CODE = "HYUNDAI"

# 서버 발급 ID — 재등록 시 제거 필수
_SERVER_KEYS = {
    "sellerProductId",
    "productId",
    "approvalStatus",
    "statusName",
    "exposedStatusName",
    "createdAt",
    "updatedAt",
}
_ITEM_SERVER_KEYS = {"vendorItemId", "itemId"}


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
    data = {k: v for k, v in data.items() if k not in _SERVER_KEYS}
    items = data.get("items")
    if isinstance(items, list):
        data["items"] = [
            {k: v for k, v in item.items() if k not in _ITEM_SERVER_KEYS}
            if isinstance(item, dict)
            else item
            for item in items
        ]
    return data


def inject_model_no(data: dict, style_code: str) -> dict:
    """items[*].modelNo, emptyBarcode 주입."""
    if not style_code:
        return data
    items = data.get("items")
    if not isinstance(items, list):
        return data
    new_items = []
    for item in items:
        if not isinstance(item, dict):
            new_items.append(item)
            continue
        item = dict(item)
        item["modelNo"] = style_code[:50]
        item["barcode"] = item.get("barcode") or ""
        item["emptyBarcode"] = not item["barcode"]
        if item["emptyBarcode"]:
            item["emptyBarcodeReason"] = "품번(MPN)으로 대체"
        new_items.append(item)
    data["items"] = new_items
    return data


async def get_samba_info(conn, spid: str) -> tuple[str, str]:
    """삼바 DB에서 (style_code, brand) 반환."""
    row = await conn.fetchrow(
        """
        SELECT style_code, brand
        FROM samba_collected_product
        WHERE market_product_nos ->> $1 = $2
        LIMIT 1
        """,
        ACCOUNT_ID,
        spid,
    )
    if not row:
        return "", ""
    return (row["style_code"] or "").strip(), (row["brand"] or "").strip()


async def update_db(conn, old_spid: str, new_spid: str) -> int:
    result = await conn.execute(
        """
        UPDATE samba_collected_product
        SET market_product_nos = jsonb_set(
            COALESCE(market_product_nos, '{}'),
            ARRAY[$1],
            to_jsonb(CAST($2 AS text))
        )
        WHERE market_product_nos ->> $1 = $3
        """,
        ACCOUNT_ID,
        new_spid,
        old_spid,
    )
    return int(result.split()[-1]) if result else 0


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

    # ─── STEP 1: DELETED 상태 상품 — 삼바 DB에 매핑된 것만 재등록 ───────
    print("\n[STEP 1] DELETED 상품 중 삼바 매핑 상품 재등록...")
    deleted_list = await client.list_seller_products(status="DELETED")
    print(f"  DELETED 전체: {len(deleted_list):,}개")

    # 삼바 DB에 매핑된 spid만 처리
    conn = await get_db_conn()
    deleted_spids = [d["seller_product_id"] for d in deleted_list]
    # 50개씩 배치 조회
    mapped_spids = set()
    for i in range(0, len(deleted_spids), 50):
        batch = deleted_spids[i : i + 50]
        rows = await conn.fetch(
            """
            SELECT market_product_nos ->> $1 AS spid
            FROM samba_collected_product
            WHERE market_product_nos ->> $1 = ANY($2)
            """,
            ACCOUNT_ID,
            batch,
        )
        for r in rows:
            if r["spid"]:
                mapped_spids.add(r["spid"])
    await conn.close()

    target_deleted = [d for d in deleted_list if d["seller_product_id"] in mapped_spids]
    print(f"  삼바 매핑 DELETED: {len(target_deleted):,}개 처리 예정")

    s1_ok, s1_fail = 0, 0
    for item in target_deleted:
        spid = item["seller_product_id"]
        ok = await process_one(client, spid, vendor_id, is_deleted=True)
        if ok:
            s1_ok += 1
        else:
            s1_fail += 1
        await asyncio.sleep(0.5)

    # ─── STEP 2: DENIED 상품 처리 ─────────────────────────────────────
    print("\n[STEP 2] DENIED 상품 처리 중...")
    denied_list = await client.list_seller_products(status="DENIED")
    print(f"  DENIED: {len(denied_list):,}개")

    s2_ok, s2_fail, s2_skip = 0, 0, 0
    for item in denied_list:
        spid = item["seller_product_id"]
        result = await process_one(client, spid, vendor_id, is_deleted=False)
        if result is True:
            s2_ok += 1
        elif result is None:
            s2_skip += 1
        else:
            s2_fail += 1
        await asyncio.sleep(0.5)

    print("\n  ═══════════════════════════════════════════════")
    print(f"  STEP1(삭제복구): 성공 {s1_ok:,} / 실패 {s1_fail:,}")
    print(f"  STEP2(DENIED):  성공 {s2_ok:,} / 실패 {s2_fail:,} / skip {s2_skip:,}")
    print("  ═══════════════════════════════════════════════")


async def process_one(client, spid: str, vendor_id: str, is_deleted: bool):
    try:
        # GET
        resp = await client.get_product(spid)
        if not isinstance(resp, dict):
            print(f"  ✗ {spid} — get_product 오류")
            return False

        data = resp.get("data", resp) if "data" in resp else resp
        if not isinstance(data, dict):
            print(f"  ✗ {spid} — data 없음")
            return False

        current_code = data.get("deliveryCompanyCode", "")
        # DENIED 상품이 이미 롯데택배이면 skip (재등록 불필요)
        if not is_deleted and current_code == LOTTE_CODE:
            return None

        # 삼바 DB에서 style_code, brand 조회
        conn = await get_db_conn()
        style_code, brand = await get_samba_info(conn, spid)
        await conn.close()

        # 서버 ID 제거 + 택배사 변경
        post_data = strip_server_ids(data)
        post_data["deliveryCompanyCode"] = LOTTE_CODE
        post_data["vendorId"] = vendor_id
        post_data["vendorUserId"] = vendor_id

        # 품번(modelNo) 주입
        if style_code:
            post_data = inject_model_no(post_data, style_code)
        else:
            # style_code 없어도 emptyBarcode 처리
            items = post_data.get("items") or []
            for item in items:
                if isinstance(item, dict):
                    if not item.get("barcode"):
                        item["emptyBarcode"] = True
                        item["emptyBarcodeReason"] = "바코드 없음"

        # brandId 보강 (삼바 DB brand 또는 기존 brand 필드)
        brand_name = brand or (data.get("brand") or "")
        if brand_name and not post_data.get("brandId"):
            try:
                brand_id = await client.search_brand_id(brand_name)
                if brand_id:
                    post_data["brandId"] = brand_id
            except Exception:
                pass  # brandId 실패 시 brand 문자열만으로 진행

        # DENIED 상품은 DELETE 후 재등록
        if not is_deleted:
            await client.delete_product(spid)
            await asyncio.sleep(0.3)

        # POST (신규 등록)
        reg = await client.register_product(post_data)
        new_spid = ""
        if isinstance(reg, dict):
            inner = reg.get("data", {})
            if isinstance(inner, dict):
                new_spid = str(inner.get("data", "") or "")
            elif inner:
                new_spid = str(inner)

        if not new_spid or not new_spid.isdigit():
            print(f"  ✗ {spid} — 재등록 실패: {str(reg)[:120]}")
            return False

        # DB 업데이트
        conn = await get_db_conn()
        updated = await update_db(conn, spid, new_spid)
        await conn.close()

        name = (data.get("sellerProductName") or "")[:35]
        db_mark = f"DB:{updated}" if updated else "DB:미매핑"
        print(
            f"  ✓ {spid}→{new_spid} ({name}) [{current_code}→{LOTTE_CODE}] [{db_mark}]"
        )
        return True

    except Exception as e:
        print(f"  ✗ {spid} — 오류: {e}")
        return False


if __name__ == "__main__":
    asyncio.run(main())
