"""무신사 claim 페이지 JS chunks 다운로드 + cancel endpoint grep."""

import json
import re
from urllib.request import Request, urlopen

CHUNK_URLS_JS = "JSON.stringify(Array.from(document.querySelectorAll('script[src]')).map(s => s.src))"

# CDP로 chunk URL 수집 (lightweight)
import asyncio
import websockets


def find_tab():
    for t in json.loads(urlopen("http://localhost:9223/json").read()):
        if t.get("type") != "page":
            continue
        u = t.get("url", "")
        if "musinsa.com/order/claim/order-cancel" in u:
            return t
    return None


async def get_chunks():
    tab = find_tab()
    if not tab:
        return []
    async with websockets.connect(tab["webSocketDebuggerUrl"], max_size=20_000_000) as ws:
        await ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {"expression": CHUNK_URLS_JS, "returnByValue": True}}))
        while True:
            e = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
            if e.get("id") == 1:
                v = e["result"]["result"]["value"]
                return json.loads(v) if isinstance(v, str) else v


def main():
    chunks = asyncio.run(get_chunks())
    print(f"chunks={len(chunks)}")
    for u in chunks:
        if "msscdn" not in u and "musinsa" not in u:
            continue
        if not u.endswith(".js"):
            continue
        try:
            req = Request(u, headers={"User-Agent": "Mozilla/5.0"})
            data = urlopen(req, timeout=20).read().decode("utf-8", errors="replace")
        except Exception as e:
            print(f"FAIL {u[-60:]}: {e}")
            continue
        # cancel / claim / refund endpoint pattern
        results = set()
        for p in [
            r'["\']([^"\']*claim/store/mypage[^"\']{0,80})["\']',
            r'["\']([^"\']*order[/-]cancel[^"\']{0,80})["\']',
            r'["\']([^"\']*api2/[^"\']*cancel[^"\']{0,80})["\']',
            r'["\']([^"\']*api2/claim[^"\']{0,120})["\']',
            r'["\']([^"\']*v1/order-items[^"\']{0,80})["\']',
        ]:
            for m in re.findall(p, data, re.IGNORECASE):
                if 8 < len(m) < 180:
                    results.add(m)
        if results:
            print(f"\n--- {u[-70:]} ({len(data)} bytes) ---")
            for r in sorted(results)[:30]:
                print(f"  {r}")


if __name__ == "__main__":
    main()
