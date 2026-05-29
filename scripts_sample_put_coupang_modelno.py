"""샘플 1건 PUT 테스트 — GET → modelNo 박은 후 update_product.

흐름
----
1. 9,387건 대상 중 첫 1건 선택
2. GET → 화이트리스트로 PUT body 정제
3. items[].modelNo = DB.style_code 박음
4. update_product PUT
5. 다시 GET → modelNo 박혔는지 검증

위험점
------
- read-only 필드(statusName/mdId/sellerProductItemId/vendorItemId 등) 처리: 쿠팡 PUT 의
  옵션 매칭 키 미확인. itemName 기준 매칭 추정 — 화이트리스트에서 옵션식별자 제거 시
  잘못 매칭될 가능성. → 1차 시도는 식별자 포함, 거부 응답이면 제거 후 재시도.
"""

import asyncio
import json
from typing import Any

import asyncpg

from backend.core.config import settings
from backend.domain.samba.proxy.coupang import CoupangClient


# transform_product 출력 키 기반 화이트리스트 (PUT 안전)
TOP_KEEP = {
    "displayCategoryCode",
    "sellerProductName",
    "vendorId",
    "saleStartedAt",
    "saleEndedAt",
    "displayProductName",
    "brand",
    "brandId",
    "generalProductName",
    "productGroup",
    "deliveryMethod",
    "deliveryCompanyCode",
    "deliveryChargeType",
    "deliveryCharge",
    "freeShipOverAmount",
    "deliveryChargeOnReturn",
    "deliverySurcharge",
    "remoteAreaDeliverable",
    "bundlePackingDelivery",
    "unionDeliveryType",
    "returnCenterCode",
    "returnChargeName",
    "companyContactNumber",
    "returnZipCode",
    "returnAddress",
    "returnAddressDetail",
    "returnCharge",
    "outboundShippingPlaceCode",
    "vendorUserId",
    "requested",
    "items",
    "requiredDocuments",
    "extraInfoMessage",
    "manufacture",
    "searchTags",
}

ITEM_KEEP = {
    "sellerProductItemId",
    "vendorItemId",
    "offerCondition",
    "offerDescription",
    "itemName",
    "originalPrice",
    "salePrice",
    "supplyPrice",
    "maximumBuyCount",
    "maximumBuyForPerson",
    "outboundShippingTimeDay",
    "maximumBuyForPersonPeriod",
    "unitCount",
    "adultOnly",
    "taxType",
    "parallelImported",
    "overseasPurchased",
    "externalVendorSku",
    "pccNeeded",
    "bestPriceGuaranteed3P",
    "emptyBarcode",
    "emptyBarcodeReason",
    "barcode",
    "modelNo",
    "extraProperties",
    "certifications",
    "searchTags",
    "images",
    "notices",
    "attributes",
    "contents",
    "sameDayShipping",
}


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
        # 샘플 1건 — 가디 계정 등록 + style_code 있음
        row = await conn.fetchrow(
            """
            SELECT id, style_code, market_product_nos, name
            FROM samba_collected_product
            WHERE status = 'registered'
              AND style_code IS NOT NULL
              AND BTRIM(style_code) <> ''
              AND market_product_nos::text LIKE '%ma_01KNZV0ZWXW52W0G4TYG3AJH9Q%'
            ORDER BY updated_at DESC
            LIMIT 1
            """
        )
        if not row:
            print("[종료] 샘플 row 없음")
            return

        mpn_raw = row["market_product_nos"]
        mpn = json.loads(mpn_raw) if isinstance(mpn_raw, str) else (mpn_raw or {})
        acc_id = "ma_01KNZV0ZWXW52W0G4TYG3AJH9Q"
        spid = mpn.get(acc_id)
        if not spid:
            print(f"[종료] spid 없음 mpn={mpn}")
            return

        style_code = (row["style_code"] or "").strip()
        print(f"[샘플] pid={row['id']} spid={spid} style_code='{style_code}' name='{row['name'][:60]}'")

        # 쿠팡 계정 인증
        acc = await conn.fetchrow(
            """
            SELECT api_key, api_secret, seller_id, additional_fields
            FROM samba_market_account WHERE id = $1
            """,
            acc_id,
        )
        af_raw = acc["additional_fields"]
        af = json.loads(af_raw) if isinstance(af_raw, str) else (af_raw or {})
        client = CoupangClient(
            access_key=(acc["api_key"] or af.get("accessKey") or "").strip(),
            secret_key=(acc["api_secret"] or af.get("secretKey") or "").strip(),
            vendor_id=(acc["seller_id"] or af.get("vendorId") or "").strip(),
        )

        # STEP A: GET
        print(f"\n[A] GET seller-products/{spid}")
        get_resp = await client.get_product(spid)
        body = get_resp.get("data") if isinstance(get_resp, dict) else get_resp
        if isinstance(body, dict) and "data" in body and isinstance(body["data"], dict):
            body = body["data"]

        before_models = [str((it or {}).get("modelNo") or "") for it in body.get("items", [])]
        print(f"    before items[].modelNo: {before_models}")

        # STEP B: PUT body 정제
        put_body: dict[str, Any] = {k: v for k, v in body.items() if k in TOP_KEEP}
        new_items = []
        for it in body.get("items", []):
            if not isinstance(it, dict):
                continue
            ni = {k: v for k, v in it.items() if k in ITEM_KEEP}
            ni["modelNo"] = style_code
            new_items.append(ni)
        put_body["items"] = new_items
        put_body.setdefault("requested", True)
        put_body.setdefault("requiredDocuments", [])
        put_body.setdefault("extraInfoMessage", "")

        # 정제된 키 출력
        print(f"\n[B] PUT body top-keys: {sorted(put_body.keys())}")
        print(f"    items[0] keys: {sorted(new_items[0].keys()) if new_items else []}")
        print(f"    items[0].modelNo: {new_items[0].get('modelNo')!r}")
        print(f"    items[0].vendorItemId: {new_items[0].get('vendorItemId')!r}")
        print(f"    items[0].sellerProductItemId: {new_items[0].get('sellerProductItemId')!r}")

        # STEP C: PUT
        print("\n[C] PUT update_product 호출")
        put_resp = await client.update_product(spid, put_body)
        print(f"    PUT 응답: {json.dumps(put_resp, ensure_ascii=False)[:600]}")

        # STEP D: 재GET → modelNo 검증
        await asyncio.sleep(2.0)
        print("\n[D] 재GET → modelNo 검증")
        recheck = await client.get_product(spid)
        rbody = recheck.get("data") if isinstance(recheck, dict) else recheck
        if isinstance(rbody, dict) and "data" in rbody and isinstance(rbody["data"], dict):
            rbody = rbody["data"]
        after_models = [str((it or {}).get("modelNo") or "") for it in rbody.get("items", [])]
        print(f"    after  items[].modelNo: {after_models}")

        ok_cnt = sum(1 for m in after_models if m.strip() == style_code)
        print(f"\n[결과] 박힌 옵션 {ok_cnt}/{len(after_models)}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
