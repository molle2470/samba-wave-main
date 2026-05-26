"""LOTTEON 취소 페이지 JS chunk 다운로드 + cancel endpoint grep."""

import asyncio
import json
import re
from urllib.request import Request, urlopen
import websockets


def find_tab():
    for t in json.loads(urlopen("http://localhost:9223/json").read()):
        if t.get("type") == "page" and "lotteon" in t.get("url", "").lower():
            return t
    return None


async def get_chunks(tab_ws):
    async with websockets.connect(tab_ws, max_size=80_000_000) as ws:
        await ws.send(json.dumps({"id":1,"method":"Runtime.evaluate","params":{"expression":"JSON.stringify(Array.from(document.querySelectorAll('script[src]')).map(s=>s.src))","returnByValue":True}}))
        while True:
            try: raw = await asyncio.wait_for(ws.recv(), timeout=10)
            except: return []
            e = json.loads(raw)
            if e.get("id") == 1:
                v = e["result"]["result"]["value"]
                return json.loads(v) if isinstance(v, str) else v


def main():
    tab = find_tab()
    if not tab: print("no tab"); return
    chunks = asyncio.run(get_chunks(tab["webSocketDebuggerUrl"]))
    print(f"chunks={len(chunks)}")
    for u in chunks:
        if "lotteon" not in u: continue
        if not u.endswith(".js"): continue
        try:
            req = Request(u, headers={"User-Agent": "Mozilla/5.0"})
            data = urlopen(req, timeout=20).read().decode("utf-8", errors="replace")
        except Exception as e:
            continue
        # cancel/claim endpoint pattern
        results = set()
        for p in [
            r'["\']([^"\']*pbf\.lotteon\.com[^"\']*claim[^"\']*)["\']',
            r'["\']([^"\']*/order/claim/v\d+/[^"\']{0,80})["\']',
            r'["\']([^"\']*Cancel[A-Za-z]*)["\']',
        ]:
            for m in re.findall(p, data, re.IGNORECASE):
                if 8 < len(m) < 200 and ('claim' in m.lower() or 'cancel' in m.lower()):
                    results.add(m)
        if results:
            print(f"\n--- {u[-70:]} ({len(data)}b) ---")
            for r in sorted(results)[:30]:
                print(f"  {r}")


if __name__ == "__main__":
    main()
