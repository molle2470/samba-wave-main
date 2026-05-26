"""SNKRDUNK 트레이딩카드 상세/리스팅 API 구조 탐색."""

import asyncio
import json
import sys

import httpx

BASE = "https://snkrdunk.com"
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


async def probe(c, url, label):
    print(f"\n### {label}: {url}")
    try:
        r = await c.get(url)
        ct = r.headers.get("content-type", "")
        print(f"  status={r.status_code} ct={ct} len={len(r.text)}")
        if "json" in ct:
            data = r.json()
            if isinstance(data, dict):
                print(f"  keys={list(data.keys())}")
                print(json.dumps(data, ensure_ascii=False, indent=1)[:2500])
            else:
                print(json.dumps(data, ensure_ascii=False)[:2000])
        else:
            # HTML — JSON-LD 추출
            import re

            for m in re.finditer(
                r'<script type="application/ld\+json">(.+?)</script>', r.text, re.DOTALL
            ):
                print(f"  JSON-LD: {m.group(1).strip()[:1500]}")
    except Exception as e:
        print(f"  ERR {e}")


async def main(cid: str):
    async with httpx.AsyncClient(
        headers=HEADERS, timeout=20.0, follow_redirects=True
    ) as c:
        # 후보 엔드포인트들 탐색
        await probe(c, f"{BASE}/en/trading-cards/{cid}", "detail HTML")
        await probe(c, f"{BASE}/en/v1/trading-cards/{cid}", "v1 detail")
        await probe(c, f"{BASE}/en/v1/trading-cards/{cid}/used", "v1 used listings")
        await probe(
            c,
            f"{BASE}/en/v1/trading-cards/{cid}/used?sort=latest&isOnlyOnSale=true",
            "v1 used onSale",
        )
        await probe(
            c, f"{BASE}/en/v1/trading-cards/{cid}/listings", "v1 listings"
        )
        await probe(c, f"{BASE}/en/v1/trading-cards/{cid}/sales", "v1 sales")


if __name__ == "__main__":
    cid = sys.argv[1] if len(sys.argv) > 1 else "671486"
    asyncio.run(main(cid))
