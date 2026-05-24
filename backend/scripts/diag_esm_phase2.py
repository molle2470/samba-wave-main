"""ESM Phase 2 — transform_product 변환 검증. 무변경.

수집 상품 1건 + 실제 leaf 카테고리코드로 gmarket/auction 변환 후 필수필드 점검.
transform 자체는 API 호출 없음. leaf catCode는 get_categories 드릴로 1개 확보.
"""

import asyncio
import json


def _drill_leaf(cats: dict, depth: int = 0) -> str:
    """get_categories 응답에서 첫 leaf catCode 1개 추출 (얕은 드릴)."""
    if not isinstance(cats, dict):
        return ""
    if cats.get("isLeaf"):
        return cats.get("catCode", "")
    for sub in cats.get("subCats", []) or []:
        if sub.get("isLeaf"):
            return sub.get("catCode", "")
    return ""


async def main() -> None:
    from sqlmodel import select

    from backend.db.orm import get_write_session
    from backend.domain.samba.account.model import SambaMarketAccount
    from backend.domain.samba.collector.model import SambaCollectedProduct
    from backend.domain.samba.proxy.esmplus import (
        ESMPlusClient,
        resolve_esm_credentials,
    )

    out: dict = {}
    async with get_write_session() as session:
        # 옵션·이미지 있는 수집 상품 1건
        prod_row = (
            (
                await session.execute(
                    select(SambaCollectedProduct)
                    .where(SambaCollectedProduct.sale_price > 0)
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        if not prod_row:
            print("PHASE2_REPORT=" + json.dumps({"error": "수집상품 없음"}))
            return
        product = {
            "name": prod_row.name,
            "market_names": getattr(prod_row, "market_names", None) or {},
            "sale_price": prod_row.sale_price,
            "original_price": getattr(prod_row, "original_price", 0),
            "images": getattr(prod_row, "images", None) or [],
            "detail_images": getattr(prod_row, "detail_images", None) or [],
            "options": getattr(prod_row, "options", None) or [],
            "brand": getattr(prod_row, "brand", ""),
            "stock": getattr(prod_row, "stock", 0),
            "source_site": getattr(prod_row, "source_site", ""),
            "detail_html": getattr(prod_row, "detail_html", "") or "",
        }
        out["sample_product"] = {
            "id": prod_row.id,
            "name": (prod_row.name or "")[:40],
            "sale_price": prod_row.sale_price,
            "img_count": len(product["images"]),
            "opt_count": len(product["options"]),
            "source": product["source_site"],
        }

        # 사이트별 leaf catCode 확보 (읽기) + 변환
        for site, mt in [("gmarket", "gmarket"), ("auction", "auction")]:
            r: dict = {}
            acc = (
                (
                    await session.execute(
                        select(SambaMarketAccount)
                        .where(SambaMarketAccount.market_type == mt)
                        .limit(1)
                    )
                )
                .scalars()
                .first()
            )
            seller_id = (getattr(acc, "api_key", "") or "") or (
                getattr(acc, "seller_id", "") or ""
            )
            hosting_id, secret_key = await resolve_esm_credentials(session, acc)
            leaf = ""
            try:
                client = ESMPlusClient(hosting_id, secret_key, seller_id, site=site)
                top = await client.get_categories()
                # 1단계 subCat 의 하위까지 한 번 더 드릴
                first_sub = (top.get("subCats") or [{}])[0]
                if first_sub.get("catCode"):
                    deeper = await client.get_categories(first_sub["catCode"])
                    leaf = _drill_leaf(deeper) or first_sub.get("catCode", "")
                await client.aclose()
            except Exception as e:
                r["catcode_err"] = f"{type(e).__name__}: {e}"
            leaf = leaf or "200000001"
            r["category_id_used"] = leaf

            try:
                data = ESMPlusClient.transform_product(product, leaf, site=site)
                # 필수필드 채움 여부 점검
                basic = data.get("itemBasicInfo", {})
                addl = data.get("itemAddtionalInfo", {})
                goods_name = (basic.get("goodsName") or {}).get("kor", "")
                cat_site = ((basic.get("category") or {}).get("site") or [{}])[0]
                price = addl.get("price") or {}
                stock = addl.get("stock") or {}
                shipping = addl.get("shipping") or {}
                images_field = (
                    basic.get("image") or addl.get("image") or data.get("image")
                )
                r["transform"] = {
                    "ok": True,
                    "top_keys": list(data.keys()),
                    "basic_keys": list(basic.keys())
                    if isinstance(basic, dict)
                    else None,
                    "addl_keys": list(addl.keys()) if isinstance(addl, dict) else None,
                    "goods_name": goods_name[:40],
                    "goods_name_filled": bool(goods_name),
                    "category_siteType": cat_site.get("siteType"),
                    "category_catCode": cat_site.get("catCode"),
                    "price": price,
                    "price_filled": bool(price),
                    "stock": stock,
                    "shipping_keys": list(shipping.keys())
                    if isinstance(shipping, dict)
                    else None,
                    "has_image_field": images_field is not None,
                    "images_val": str(addl.get("images"))[:200],
                    "recommendedOpts_val": str(addl.get("recommendedOpts"))[:250],
                    "shipping_val": str(shipping)[:200],
                    "descriptions_filled": bool(addl.get("descriptions")),
                    "officialNotice_filled": bool(addl.get("officialNotice")),
                }
            except Exception as e:
                import traceback

                r["transform"] = {
                    "ok": False,
                    "err": f"{type(e).__name__}: {e}",
                    "tb": traceback.format_exc()[-400:],
                }
            out[site] = r

    print("PHASE2_REPORT=" + json.dumps(out, ensure_ascii=False, default=str))


asyncio.run(main())
