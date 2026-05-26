"""LOTTEON confirm dialog accept + POST 캡처."""

import asyncio
import json
from urllib.request import urlopen
import websockets


ORD = "2026052612069650"


def find_tab():
    for t in json.loads(urlopen("http://localhost:9223/json").read()):
        u = t.get("url", "")
        if t.get("type") == "page" and "lotteon.com" in u and ORD in u:
            return t
    return None


async def main():
    tab = find_tab()
    if not tab:
        print("no tab")
        return
    print(f"TAB: {tab['url']}")
    async with websockets.connect(tab["webSocketDebuggerUrl"], max_size=80_000_000) as ws:
        mid = [0]
        async def call(m, p=None, timeout=10.0):
            mid[0] += 1
            await ws.send(json.dumps({"id": mid[0], "method": m, "params": p or {}}))
            end = asyncio.get_event_loop().time() + timeout
            while asyncio.get_event_loop().time() < end:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
                except asyncio.TimeoutError:
                    continue
                e = json.loads(raw)
                if e.get("id") == mid[0]:
                    return e
            return {}

        # Page domain enable
        await call("Page.enable")
        await call("Runtime.enable")
        await call("Network.enable")
        # accept any pending dialog
        try:
            r = await call("Page.handleJavaScriptDialog", {"accept": True})
            print("DIALOG:", r)
        except Exception as e:
            print("dialog err:", e)
        await asyncio.sleep(8)

        # dump previous hook log
        for log_var in ("__sambaLOT5log", "__sambaLOT4log", "__sambaLOT3log"):
            mid[0] += 1
            await ws.send(json.dumps({"id": mid[0], "method": "Runtime.evaluate", "params": {"expression": f"JSON.stringify(window.{log_var} || [])", "returnByValue": True}}))
            while True:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                except asyncio.TimeoutError:
                    break
                e = json.loads(raw)
                if e.get("id") == mid[0]:
                    v = e["result"]["result"]["value"]
                    log = json.loads(v) if isinstance(v, str) else (v or [])
                    if log:
                        noise = ("google", "facebook", "doubleclick", "analytics", "kakao", "naver.com", "pinterest", "twitter", "hotjar", "criteo", "airbridge", "tiktok", "braze", "cloudflare", "/cdn-cgi/", "creativecdn", "datadoghq", "static.lotteon", "sdk.iad", "asia.creativecdn", "log.lotteon", "bc.ad.daum", "wcs.naver", "lotteon.com/p/static", "rec_collect", "prd-analytics", "lottePay")
                        cleaned = [e for e in log if not any(s in (e.get("url") or "").lower() for s in noise)]
                        posts = [e for e in cleaned if e.get("method") in ("POST", "PUT", "DELETE", "PATCH")]
                        if posts:
                            print(f"\n=== POSTS from {log_var} ({len(posts)}) ===")
                            print(json.dumps(posts, ensure_ascii=False, indent=2))
                            break
                    break

        # current url
        mid[0] += 1
        await ws.send(json.dumps({"id": mid[0], "method": "Runtime.evaluate", "params": {"expression": "location.href", "returnByValue": True}}))
        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
            except asyncio.TimeoutError:
                break
            e = json.loads(raw)
            if e.get("id") == mid[0]:
                print("\nFINAL_URL:", e["result"]["result"]["value"])
                break


asyncio.run(main())
