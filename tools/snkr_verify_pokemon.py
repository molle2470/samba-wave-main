"""포켓몬 트레이딩카드 3개 샘플 수집 검증 — 실제 수집 흐름(listing→detail) 미러."""

import asyncio

from backend.domain.samba.proxy.snkrdunk import SnkrdunkClient


async def main():
    c = SnkrdunkClient()
    # 1) 포켓몬 브랜드 트레이딩카드 리스트 수집 (수집 진입점과 동일 경로)
    listing = await c.collect_brand_cards(
        brand_id="pokemon", category_id="", max_count=60
    )
    cards = listing.get("products", [])
    print(f"포켓몬 카드 리스트 수집: {len(cards)}건")

    # 입찰(listingCount)>0 인 카드만 — 재고 있는 것 우선 3개
    picked = []
    for p in cards:
        ed = p.get("extra_data") or {}
        try:
            lc = int(ed.get("listing_count") or "0")
        except Exception:
            lc = 0
        if lc > 0:
            picked.append(p)
        if len(picked) >= 3:
            break

    print(f"검증 대상 3개 선정 (listingCount>0)\n")
    for p in picked:
        cid = p["site_product_id"]
        # 2) 상세 = 컨디션별 used-listings 최저가 (수집 시 worker가 호출하는 경로)
        d = await c.get_detail(cid, "trading-card")
        opts = d.get("options") or []
        print(f"=== id={cid}")
        print(f"  name: {d.get('name')}")
        print(f"  source_url: {d.get('url')}")
        print(f"  리스트 minPrice: {p.get('sale_price')} (USD)")
        print(
            f"  detail 최저가(in-stock only): {d.get('sale_price')} "
            f"status={d.get('sale_status')}"
        )
        print(f"  컨디션(옵션) {len(opts)}개:")
        for o in opts:
            print(f"     - {o['name']:<16} 최저 ${o['price']:<6} 재고 {o['stock']}")
        print()


asyncio.run(main())
