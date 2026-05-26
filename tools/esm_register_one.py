# -*- coding: utf-8 -*-
"""무신사 상품 1건을 지정 ESM 계정(G마켓 또는 옥션)에 실제 등록 + get_product 검증.

usage: python esm_register_one.py <product_id> <account_id> <site:gmarket|auction>
WRITE 세션 사용. _transmit_product(실제 마켓 전송) 호출.
"""
import asyncio
import json
import sys

from backend.db.orm import get_write_session
from backend.domain.samba.account.repository import SambaMarketAccountRepository
from backend.domain.samba.collector.repository import SambaCollectedProductRepository
from backend.domain.samba.proxy.esmplus import ESMPlusClient, resolve_esm_credentials
from backend.domain.samba.shipment.repository import SambaShipmentRepository
from backend.domain.samba.shipment.service import SambaShipmentService


async def verify_get_product(session, account, site, goods_no):
    """등록된 goodsNo를 ESM get_product로 재조회 검증."""
    extras = account.additional_fields or {}
    creds = {k: v for k, v in extras.items() if v}
    if not creds and account.api_key:
        creds["apiKey"] = account.api_key
    seller_id = (
        creds.get("apiKey", "") or creds.get("sellerId", "") or (account.seller_id or "")
    )
    hosting_id, secret_key = await resolve_esm_credentials(session, account)
    client = ESMPlusClient(hosting_id, secret_key, seller_id, site=site)
    try:
        gp = await client.get_product(str(goods_no))
        # 핵심 필드만 추려서 표시
        basic = gp.get("itemBasicInfo", {}) if isinstance(gp, dict) else {}
        addl = gp.get("itemAddtionalInfo", {}) if isinstance(gp, dict) else {}
        img = gp.get("imageModel") or gp.get("itemImageInfo") or {}
        return {
            "goodsNo": str(goods_no),
            "name": (basic.get("goodsName") or {}),
            "category": basic.get("category"),
            "price": addl.get("price"),
            "stock": addl.get("stock"),
            "image_keys": list(img.keys()) if isinstance(img, dict) else str(type(img)),
            "raw_keys": list(gp.keys()) if isinstance(gp, dict) else str(type(gp)),
        }
    except Exception as e:
        return {"goodsNo": str(goods_no), "verify_error": f"{type(e).__name__}: {e}"}
    finally:
        await client.aclose()


async def main():
    product_id = sys.argv[1]
    account_id = sys.argv[2]
    site = sys.argv[3]

    out = {"product_id": product_id, "account_id": account_id, "site": site}
    async with get_write_session() as session:
        svc = SambaShipmentService(SambaShipmentRepository(session), session)
        # 실제 전송 — 전체 필드(가격/재고/이미지/상세) 등록
        shipment = await svc._transmit_product(
            product_id,
            [account_id],
            ["price", "stock", "image", "description"],
        )
        await session.commit()

        out["shipment_status"] = shipment.status
        out["transmit_result"] = shipment.transmit_result
        out["transmit_error"] = shipment.transmit_error
        out["mapped_categories"] = getattr(shipment, "mapped_categories", None)

        # goodsNo 추출 — transmit_result(계정별) 우선, 없으면 product.market_product_nos
        goods_no = ""
        tr = shipment.transmit_result or {}
        if isinstance(tr, dict):
            goods_no = SambaShipmentService._extract_market_product_no(
                tr.get(account_id) if isinstance(tr.get(account_id), dict) else tr
            )
        if not goods_no:
            prod_repo = SambaCollectedProductRepository(session)
            p = await prod_repo.get_async(product_id)
            mpn = (p.market_product_nos or {}) if p else {}
            gv = mpn.get(account_id)
            if isinstance(gv, dict):
                goods_no = gv.get("originProductNo") or gv.get("goodsNo") or ""
            else:
                goods_no = gv or ""
        out["extracted_goodsNo"] = goods_no

        # get_product 검증
        if goods_no and shipment.status in ("completed", "partial"):
            acc_repo = SambaMarketAccountRepository(session)
            account = await acc_repo.get_async(account_id)
            out["verify"] = await verify_get_product(session, account, site, goods_no)

    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
