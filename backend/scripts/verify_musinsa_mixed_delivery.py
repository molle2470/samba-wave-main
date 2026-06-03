"""#332 무신사 혼합배송 역마진 실측 검증 — 프로덕션 실쿠키 (읽기 전용).

검증:
  1) 6123585(아디다스 가젤 로우 프로) bestBenefitPrice == 129,060 부근
  2) 옵션 21개 전부 동일가(브랜드배송 옵션도 직배송가)로 수집됨 (= 버그)
  3) inventory API raw 응답에 isRedirect + relatedOption.relatedGoodsNo(=6239489) 존재
  4) 6239489(브랜드배송 실상품) bestBenefitPrice == 163,930 부근
     → (A)안대로 relatedGoodsNo 혜택가를 옵션가로 쓰면 교정 가능한지
"""

import asyncio
import json

import httpx

from backend.api.v1.routers.samba.collector_common import get_musinsa_cookie
from backend.domain.samba.proxy.musinsa import MusinsaClient

MAIN = "6123585"
RELATED_EXPECTED = "6239489"


async def main() -> None:
    cookie = await get_musinsa_cookie()
    print(f"cookie 길이={len(cookie or '')} (있음={bool(cookie)})")
    if not cookie:
        print("쿠키 없음 — 검증 불가")
        return

    client = MusinsaClient(cookie)

    # 1) 본상품 수집
    r1 = await client.get_goods_detail(MAIN)
    print(f"\n[본상품 {MAIN}] {r1.get('name')}")
    print(f"  bestBenefitPrice            = {r1.get('bestBenefitPrice'):,}")
    print(f"  bestBenefitPriceExclHeldPoint = {r1.get('bestBenefitPriceExclHeldPoint'):,}")
    opts = r1.get("options") or []
    print(f"  옵션 수 = {len(opts)}")
    prices = sorted({o.get("price") for o in opts})
    print(f"  옵션 distinct price = {[f'{p:,}' for p in prices]}")
    bd = [o for o in opts if o.get("isBrandDelivery")]
    print(f"  isBrandDelivery=True 옵션 수 = {len(bd)}")
    for o in opts[:6]:
        print(
            f"    {o.get('name'):<20} price={o.get('price'):>8,} "
            f"brandDeliv={o.get('isBrandDelivery')} stock={o.get('stock')}"
        )

    # 3) inventory raw 덤프 — relatedOption 존재 확인 (코드가 폐기하는 필드)
    print(f"\n[inventory raw {MAIN}] relatedOption 확인")
    timeout = httpx.Timeout(30.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as c:
        # optionValueNo 수집
        opt_resp = await c.get(
            f"{client.BASE_DETAIL}/{MAIN}/options", headers=client._headers()
        )
        ovn: list[int] = []
        for it in (opt_resp.json().get("data") or {}).get("optionItems", []):
            for v in it.get("optionValues", []):
                if v.get("no"):
                    ovn.append(v["no"])
        inv_resp = await c.post(
            f"{client.BASE_DETAIL}/{MAIN}/options/v2/prioritized-inventories",
            headers=client._headers({"Content-Type": "application/json"}),
            json={"optionValueNos": ovn},
        )
        inv_data = inv_resp.json().get("data") or []
        related_nos = set()
        n_redirect = 0
        for inv in inv_data:
            if inv.get("isRedirect"):
                n_redirect += 1
                ro = inv.get("relatedOption") or {}
                if ro.get("relatedGoodsNo"):
                    related_nos.add(str(ro["relatedGoodsNo"]))
        print(f"  isRedirect=True 옵션 수 = {n_redirect}")
        print(f"  distinct relatedGoodsNo = {sorted(related_nos)}")
        # 샘플 1건 raw
        for inv in inv_data:
            if inv.get("isRedirect"):
                print("  redirect 옵션 raw 샘플:")
                print(
                    "    "
                    + json.dumps(
                        {
                            "productVariantId": inv.get("productVariantId"),
                            "isRedirect": inv.get("isRedirect"),
                            "outOfStock": inv.get("outOfStock"),
                            "relatedOption": inv.get("relatedOption"),
                        },
                        ensure_ascii=False,
                    )
                )
                break

    # 4) relatedGoodsNo 상품 혜택가
    if related_nos:
        rel = sorted(related_nos)[0]
        print(f"\n[브랜드배송 실상품 {rel}] (기대 {RELATED_EXPECTED})")
        r2 = await client.get_goods_detail(rel)
        print(f"  bestBenefitPrice            = {r2.get('bestBenefitPrice'):,}")
        print(f"  bestBenefitPriceExclHeldPoint = {r2.get('bestBenefitPriceExclHeldPoint'):,}")
        diff = (r2.get("bestBenefitPrice") or 0) - (r1.get("bestBenefitPrice") or 0)
        print(f"  본상품 대비 차이 = {diff:+,} (이만큼 옵션 과소 수집 = 역마진)")


if __name__ == "__main__":
    asyncio.run(main())
