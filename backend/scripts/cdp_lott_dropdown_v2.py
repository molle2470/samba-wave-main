"""LOTTEON v-select native mousedown으로 열고 옵션 dump."""

import asyncio
import json
from urllib.request import urlopen
import websockets


def find_tab():
    for t in json.loads(urlopen("http://localhost:9223/json").read()):
        if t.get("type") == "page" and "lotteon.com" in t.get("url", "") and "orderCancellationAccept" in t.get("url", ""):
            return t
    return None


GET_VS_RECT_JS = r"""
(() => {
  const vs = document.querySelector('div.v-select')
  if (!vs) return null
  const r = vs.getBoundingClientRect()
  return JSON.stringify({x: r.x + r.width/2, y: r.y + r.height/2, w: r.width, h: r.height})
})()
"""

DUMP_OPTIONS_JS = r"""
(async () => {
  await new Promise(r => setTimeout(r, 1000))
  // dropdown menu 직접 찾기
  const dm = document.querySelector('ul.vs__dropdown-menu')
  if (!dm) return {found: false, all_uls: Array.from(document.querySelectorAll('ul')).map(u => (typeof u.className === 'string' ? u.className : '').slice(0,80)).slice(0,20)}
  const items = []
  for (const li of dm.querySelectorAll('li')) {
    items.push({text: (li.innerText || '').trim(), role: li.getAttribute('role') || '', cls: (typeof li.className === 'string' ? li.className : '').slice(0,80)})
  }
  return {found: true, items, dm_class: (typeof dm.className === 'string' ? dm.className : '')}
})()
"""


async def main():
    tab = find_tab()
    if not tab:
        print("no cancellation tab — orderCancellationAccept 페이지 필요")
        return
    async with websockets.connect(tab["webSocketDebuggerUrl"], max_size=80_000_000) as ws:
        mid = [0]
        async def call(m, p=None):
            mid[0] += 1
            await ws.send(json.dumps({"id": mid[0], "method": m, "params": p or {}}))
            end = asyncio.get_event_loop().time() + 30
            while asyncio.get_event_loop().time() < end:
                try: raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
                except: continue
                e = json.loads(raw)
                if e.get("id") == mid[0]:
                    return e
            return {}
        async def evalJS(js):
            r = await call("Runtime.evaluate", {"expression": js, "returnByValue": True, "awaitPromise": True})
            return r.get("result", {}).get("result", {}).get("value")
        async def native(t, x, y, button="left", buttons=1):
            await call("Input.dispatchMouseEvent", {"type": t, "x": x, "y": y, "button": button, "buttons": buttons})

        await call("Page.enable")
        await call("Runtime.enable")

        rect = await evalJS(GET_VS_RECT_JS)
        print("VS RECT:", rect)
        if not rect:
            print("no v-select")
            return
        r = json.loads(rect)

        # native mousedown + mouseup + click
        await native("mouseMoved", r["x"], r["y"], buttons=0)
        await native("mousePressed", r["x"], r["y"], buttons=1)
        await native("mouseReleased", r["x"], r["y"], buttons=0)
        await asyncio.sleep(0.8)

        opts = await evalJS(DUMP_OPTIONS_JS)
        print("\nOPTIONS:", json.dumps(opts if isinstance(opts, dict) else json.loads(opts), ensure_ascii=False, indent=2))


asyncio.run(main())
