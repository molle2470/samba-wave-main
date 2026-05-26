"""SNKRDUNK 트레이딩카드 used-listings/variations/conditions 구조 확인."""

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
    "Accept": "application/json,*/*",
    "Accept-Language": "en-US,en;q=0.9,ja;q=0.8",
    "Referer": f"{BASE}/en/",
}


async def show(c, url, label):
    print(f"\n### {label}: {url}")
    r = await c.get(url)
    print(f"  status={r.status_code} ct={r.headers.get('content-type')}")
    try:
        data = r.json()
    except Exception:
        print("  non-json:", r.text[:300])
        return None
    print(json.dumps(data, ensure_ascii=False, indent=1)[:3500])
    return data


async def main(cid: str):
    pid = f"SW---{cid}"
    async with httpx.AsyncClient(
        headers=HEADERS, timeout=20.0, follow_redirects=True
    ) as c:
        await show(
            c,
            f"{BASE}/en/v1/products/{pid}/used-listings?perPage=20&page=1&sortType=latest&isOnlyOnSale=true",
            "used-listings onSale",
        )
        await show(c, f"{BASE}/en/v1/products/{pid}/variations", "variations")
        await show(
            c,
            f"{BASE}/en/v1/streetwears/used-listings/conditions",
            "conditions",
        )


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "671486"))
