"""가디 계정 쿠팡 DENIED/DELETED 상품 재등록 v5.

전략: DELETED 목록 페이징(수만 개) 대신
삼바 DB의 market_product_nos[ACCOUNT_ID] 상품들을 직접 조회.
각 spid의 쿠팡 상태 확인:
  - DENIED  → DELETE + 재등록
  - DELETED → 바로 재등록
  - APPROVED/APPROVING/IN_REVIEW/SAVED → skip

재등록 시 추가:
  - deliveryCompanyCode = HYUNDAI (롯데택배)
  - modelNo = style_code (품번 의무화)
  - brandId 검색 주입 (브랜드ID 의무화)
  - emptyBarcode = True
  - 서버 발급 ID 제거 (sellerProductId, vendorItemId 등)
  - 삼바 DB market_product_nos 업데이트
"""

import asyncio
import json
import asyncpg
from backend.core.config import settings

ACCOUNT_ID = "ma_01KNZV0ZWXW52W0G4TYG3AJH9Q"
LOTTE_CODE = "HYUNDAI"
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
SKIP_STATUSES = {"APPROVED", "PARTIAL_APPROVED", "APPROVING", "IN_REVIEW", "SAVED"}


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
    items = data.get("items")
    if not isinstance(items, list):
        return data
    new_items = []
    for item in items:
        if not isinstance(item, dict):
            new_items.append(item)
            continue
        item = dict(item)
        if style_code:
            item["modelNo"] = style_code[:50]
        barcode = item.get("barcode") or ""
        item["barcode"] = barcode
        item["emptyBarcode"] = not barcode
        if item["emptyBarcode"]:
            item["emptyBarcodeReason"] = (
                "품번(MPN)으로 대체" if style_code else "바코드 없음"
            )
        new_items.append(item)
    data["items"] = new_items
    return data


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
    # 계정 인증정보
    conn = await get_db_conn()
    acct = await conn.fetchrow(
        "SELECT account_label, additional_fields FROM samba_market_account WHERE id = $1",
        ACCOUNT_ID,
    )
    if not acct:
        print(f"계정 {ACCOUNT_ID} 없음")
        await conn.close()
        return

    label = acct["account_label"]
    raw = acct["additional_fields"] or {}
    extras = json.loads(raw) if isinstance(raw, str) else (raw or {})
    access_key = extras.get("accessKey") or ""
    secret_key = extras.get("secretKey") or ""
    vendor_id = extras.get("vendorId") or ""

    if not access_key or not secret_key or not vendor_id:
        print("인증정보 누락")
        await conn.close()
        return

    print(f"▶ 계정: {label} ({ACCOUNT_ID})")

    # 삼바 DB에서 가디 계정에 등록된 상품 목록 (spid, style_code, brand)
    rows = await conn.fetch(
        """
        SELECT
            market_product_nos ->> $1 AS spid,
            style_code,
            brand
        FROM samba_collected_product
        WHERE market_product_nos ? $1
          AND (market_product_nos ->> $1) IS NOT NULL
          AND (market_product_nos ->> $1) != ''
        """,
        ACCOUNT_ID,
    )
    await conn.close()

    print(f"  삼바 DB 매핑 상품: {len(rows):,}개")

    from backend.domain.samba.proxy.coupang import CoupangClient

    client = CoupangClient(access_key, secret_key, vendor_id)

    ok, fail, skip = 0, 0, 0
    fail_log = []

    for i, row in enumerate(rows, 1):
        spid = row["spid"]
        style_code = (row["style_code"] or "").strip()
        brand = (row["brand"] or "").strip()

        try:
            # 상품 현재 상태/데이터 GET
            resp = await client.get_product(spid)
            if not isinstance(resp, dict):
                fail += 1
                fail_log.append(f"{spid}: get_product 오류")
                continue

            data = resp.get("data", resp) if "data" in resp else resp
            if not isinstance(data, dict):
                fail += 1
                fail_log.append(f"{spid}: data 없음")
                continue

            # status 영어 코드 사용 (statusName은 한국어라 체크 불가)
            status = (data.get("status") or "").upper()
            current_code = data.get("deliveryCompanyCode", "")

            # DENIED / DELETED 외 모두 skip (APPROVED, APPROVING, IN_REVIEW 등)
            if status not in ("DENIED", "DELETED"):
                skip += 1
                if i % 200 == 0:
                    print(
                        f"  [{i:,}/{len(rows):,}] skip={skip:,} ok={ok:,} fail={fail:,}"
                    )
                continue

            is_deleted = status == "DELETED"

            # 서버 ID 제거 + 택배사 변경
            post_data = strip_server_ids(data)
            post_data["deliveryCompanyCode"] = LOTTE_CODE
            post_data["vendorId"] = vendor_id
            post_data["vendorUserId"] = vendor_id
            post_data["requested"] = True  # 임시저장 방지 — 등록 즉시 승인요청

            # 품번 + emptyBarcode 주입
            post_data = inject_model_no(post_data, style_code)

            # brandId 보강
            brand_name = brand or (data.get("brand") or "")
            if brand_name and not post_data.get("brandId"):
                try:
                    bid = await client.search_brand_id(brand_name)
                    if bid:
                        post_data["brandId"] = bid
                except Exception:
                    pass

            # DENIED → DELETE 후 재등록 / DELETED → 바로 재등록
            if not is_deleted:
                await client.delete_product(spid)
                await asyncio.sleep(0.3)

            reg = await client.register_product(post_data)
            new_spid = ""
            if isinstance(reg, dict):
                inner = reg.get("data", {})
                if isinstance(inner, dict):
                    new_spid = str(inner.get("data", "") or "")
                elif inner:
                    new_spid = str(inner)

            if not new_spid or not new_spid.isdigit():
                err_msg = str(reg)[:150]
                fail += 1
                fail_log.append(f"{spid}({status}): 재등록 실패 — {err_msg}")
                print(f"  ✗ {spid} [{status}] 재등록 실패: {err_msg}")
                # 일일 구매옵션 한도 초과 → 더 진행하면 DELETE만 되고 재등록 실패 반복
                if (
                    "오늘 등록할 수 있는 구매옵션 개수" in err_msg
                    or "초과하였습니다" in err_msg
                ):
                    print("\n  ⚠️  일일 구매옵션 등록 한도 초과. 내일 다시 실행하세요.")
                    print(
                        f"  현재까지 성공: {ok:,}개 / 실패: {fail:,}개 / skip: {skip:,}개"
                    )
                    break
                continue

            # DB 업데이트
            conn2 = await get_db_conn()
            updated = await update_db(conn2, spid, new_spid)
            await conn2.close()

            name = (data.get("sellerProductName") or "")[:30]
            ok += 1
            print(
                f"  ✓ {spid}→{new_spid} [{status}→등록] [{current_code}→{LOTTE_CODE}] DB:{updated} ({name})"
            )

            await asyncio.sleep(0.5)

        except Exception as e:
            fail += 1
            fail_log.append(f"{spid}: {e}")
            print(f"  ✗ {spid} — {e}")

        if i % 50 == 0:
            print(f"  [{i:,}/{len(rows):,}] ok={ok:,} fail={fail:,} skip={skip:,}")

    print("\n  ═══════════════════════════════════════════════")
    print(f"  완료: 성공 {ok:,} / 실패 {fail:,} / skip {skip:,}")
    if fail_log:
        print(f"\n  실패 목록 ({len(fail_log)}개):")
        for f in fail_log[:30]:
            print(f"    {f}")
        if len(fail_log) > 30:
            print(f"    ... 외 {len(fail_log) - 30}개")


if __name__ == "__main__":
    asyncio.run(main())
