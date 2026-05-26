"""각 LOTTEON 탭에서 hook log dump."""

import asyncio
import json
from urllib.request import urlopen
import websockets


async def dump(ws_url, label):
    try:
        async with websockets.connect(ws_url, max_size=80_000_000) as ws:
            for var in ("__sambaLOT5log", "__sambaLOT4log", "__sambaLOT3log", "__sambaLotteonLog2", "__sambaLotteonLog"):
                await ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {"expression": f"JSON.stringify(window.{var} || [])", "returnByValue": True}}))
                while True:
                    try: raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    except: break
                    e = json.loads(raw)
                    if e.get("id") == 1:
                        v = e["result"]["result"]["value"]
                        log = json.loads(v) if isinstance(v, str) else (v or [])
                        if log:
                            noise = ("google","facebook","doubleclick","analytics","kakao","naver.com","pinterest","twitter","hotjar","criteo","airbridge","tiktok","braze","cloudflare","/cdn-cgi/","creativecdn","datadoghq","static.lotteon","sdk.iad","asia.creativecdn","log.lotteon","bc.ad.daum","wcs.naver","lotteon.com/p/static","rec_collect","prd-analytics","facebook","linkedin")
                            cleaned = [x for x in log if not any(s in (x.get("url") or "").lower() for s in noise)]
                            posts = [x for x in cleaned if x.get("method") in ("POST","PUT","DELETE","PATCH")]
                            if posts:
                                print(f"\n=== {label} | {var} POSTS ({len(posts)}) ===")
                                print(json.dumps(posts, ensure_ascii=False, indent=2))
                            elif cleaned:
                                print(f"\n--- {label} | {var} GETs ({len(cleaned)}) ---")
                                for x in cleaned[:5]: print(x.get("method"), x.get("url"), x.get("status"))
                        break
                break  # one var, then break
    except Exception as e:
        print(f"{label} ERR: {e}")


async def main():
    tabs = [t for t in json.loads(urlopen("http://localhost:9223/json").read()) if t.get("type") == "page" and "lotteon" in t.get("url", "").lower()]
    for t in tabs:
        await dump(t["webSocketDebuggerUrl"], t["url"][-80:])


asyncio.run(main())
