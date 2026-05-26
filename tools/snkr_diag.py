"""SNKRDUNK 옵션별 가격 수집 진단.

실제 사이트에서 search → detail HTML 가져와서:
  1. JSON-LD offers 에 등장하는 priceCurrency 값 전부 출력
  2. _parse_detail 결과 options 출력
추측 금지 — 실제 응답 그대로 확인용.
"""

import asyncio
import json
import re
import sys

import httpx

BASE = "https://snkrdunk.com"
SEARCH_URL = f"{BASE}/en/v1/search"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,ja;q=0.8",
    "Referer": f"{BASE}/en/",
}


def extract_jsonld(html: str):
    out = []
    for m in re.finditer(
        r'<script type="application/ld\+json">(.+?)</script>', html, re.DOTALL
    ):
        try:
            out.append(json.loads(m.group(1).strip()))
        except Exception as e:
            print(f"  JSON parse fail: {e}")
    return out


async def main(keyword: str):
    async with httpx.AsyncClient(
        headers=HEADERS, timeout=20.0, follow_redirects=True
    ) as c:
        r = await c.get(
            SEARCH_URL, params={"keyword": keyword, "perPage": 5, "page": 1, "type": ""}
        )
        print(f"search status={r.status_code}")
        data = r.json()
        print(f"sneakerCount={data.get('sneakerCount')} streetwearCount={data.get('streetwearCount')}")
        sneakers = data.get("sneakers") or []
        streets = data.get("streetwears") or []
        print(f"sneakers={len(sneakers)} streetwears={len(streets)}")

        targets = []
        if sneakers:
            targets.append(("sneaker", str(sneakers[0].get("id"))))
        if streets:
            targets.append(("streetwear", str(streets[0].get("id"))))

        for snkr_type, sid in targets:
            path = "sneakers" if snkr_type == "sneaker" else "streetwears"
            url = f"{BASE}/en/{path}/{sid}"
            print(f"\n=== {snkr_type} id={sid} {url} ===")
            dr = await c.get(url)
            print(f"  detail status={dr.status_code} len={len(dr.text)}")
            lds = extract_jsonld(dr.text)
            print(f"  jsonld nodes={len(lds)}")
            for node in lds:
                nodes = node if isinstance(node, list) else [node]
                for n in nodes:
                    if not isinstance(n, dict) or n.get("@type") != "Product":
                        continue
                    print(f"  Product name={n.get('name')!r}")
                    offers = n.get("offers") or []
                    if isinstance(offers, dict):
                        offers = [offers]
                    for agg in offers:
                        if not isinstance(agg, dict):
                            continue
                        print(
                            f"    AggregateOffer type={agg.get('@type')} "
                            f"currency={agg.get('priceCurrency')!r} "
                            f"low={agg.get('lowPrice')} high={agg.get('highPrice')} "
                            f"offerCount={agg.get('offerCount')}"
                        )
                        inner = agg.get("offers") or []
                        currencies = set()
                        for o in inner:
                            if isinstance(o, dict):
                                currencies.add(o.get("priceCurrency"))
                        print(f"      inner offers={len(inner)} currencies={currencies}")
                        for o in inner[:3]:
                            if isinstance(o, dict):
                                print(
                                    f"      sample: cur={o.get('priceCurrency')!r} "
                                    f"price={o.get('price')} desc={o.get('description')!r} "
                                    f"avail={o.get('availability')!r}"
                                )


if __name__ == "__main__":
    kw = sys.argv[1] if len(sys.argv) > 1 else "jordan"
    asyncio.run(main(kw))
