"""LOTTEON 떠 있는 JS confirm dialog 강제 accept."""

import asyncio
import json
from urllib.request import urlopen
import websockets


def find_tab():
    for t in json.loads(urlopen("http://localhost:9223/json").read()):
        u = t.get("url", "")
        if t.get("type") == "page" and "lotteon.com" in u and "cancellation" in u:
            return t
    return None


async def main():
    tab = find_tab()
    if not tab:
        print("no tab")
        return
    print(f"TAB: {tab['url']}")
    async with websockets.connect(tab["webSocketDebuggerUrl"], max_size=80_000_000) as ws:
        # Page.enable 먼저 — dialog event listener 활성화
        await ws.send(json.dumps({"id":1,"method":"Page.enable"}))
        # immediate accept
        await ws.send(json.dumps({"id":2,"method":"Page.handleJavaScriptDialog","params":{"accept":True}}))
        # wait + capture events
        end = asyncio.get_event_loop().time() + 8.0
        while asyncio.get_event_loop().time() < end:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                e = json.loads(raw)
                m = e.get("method", "")
                if "id" in e or "Dialog" in m or "Page" in m:
                    print(f"EVT: {json.dumps(e, ensure_ascii=False)[:500]}")
            except asyncio.TimeoutError:
                continue
        # 최종 URL
        await ws.send(json.dumps({"id":99,"method":"Runtime.evaluate","params":{"expression":"location.href","returnByValue":True}}))
        while True:
            try: raw = await asyncio.wait_for(ws.recv(), timeout=3.0)
            except: break
            e = json.loads(raw)
            if e.get("id") == 99:
                print("FINAL_URL:", e["result"]["result"]["value"])
                break


asyncio.run(main())
