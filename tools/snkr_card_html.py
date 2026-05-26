"""트레이딩카드 상세 HTML 내 API 경로/임베디드 JSON 추출."""

import asyncio
import re
import sys

import httpx

BASE = "https://snkrdunk.com"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,*/*",
    "Accept-Language": "en-US,en;q=0.9,ja;q=0.8",
    "Referer": f"{BASE}/en/",
}


async def main(cid: str):
    async with httpx.AsyncClient(
        headers=HEADERS, timeout=20.0, follow_redirects=True
    ) as c:
        r = await c.get(f"{BASE}/en/trading-cards/{cid}/used?sort=latest&isOnlyOnSale=true")
        html = r.text
        print(f"status={r.status_code} len={len(html)}")
        # API 경로 후보
        paths = set(re.findall(r'["\'](/(?:en/)?v1/[^"\']+)["\']', html))
        print("v1 paths in html:")
        for p in sorted(paths):
            print("  ", p)
        # trading-card 관련 경로
        tc = set(re.findall(r'["\'](/[^"\']*trading-card[^"\']*)["\']', html))
        print("trading-card paths:")
        for p in sorted(tc):
            print("  ", p)
        # __NEXT_DATA__ 또는 window 상태
        for m in re.finditer(r'id="__NEXT_DATA__"[^>]*>(.+?)</script>', html, re.DOTALL):
            print("NEXT_DATA found len=", len(m.group(1)))
            print(m.group(1)[:3000])
        # nuxt/__INITIAL_STATE__
        for kw in ("__NUXT__", "__INITIAL_STATE__", "window.__"):
            idx = html.find(kw)
            if idx >= 0:
                print(f"{kw} @ {idx}: {html[idx:idx+400]}")
        # productNumber / minPrice 등 키 등장 여부
        for kw in ("minPrice", "listingCount", "productNumber", "apiBaseUrl", "API_BASE"):
            print(f"  has '{kw}':", kw in html)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "671486"))
