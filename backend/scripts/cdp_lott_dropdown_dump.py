"""LOTTEON 사유 dropdown 열고 옵션 텍스트 dump."""

import asyncio
import json
from urllib.request import urlopen
import websockets


def find_tab():
    for t in json.loads(urlopen("http://localhost:9223/json").read()):
        if t.get("type") == "page" and "lotteon.com" in t.get("url", ""):
            return t
    return None


JS = r"""
(async () => {
  const url = 'https://www.lotteon.com/p/order/claim/cancellation/orderCancellationAccept?odNo=2026052612069650&odSeq=1&procSeq=1'
  if (!location.href.includes('orderCancellationAccept')) {
    location.href = url
    await new Promise(r => setTimeout(r, 6000))
  }
  // v-select 대기
  let vs = null
  for (let i = 0; i < 40; i++) {
    vs = document.querySelector('div.v-select')
    if (vs) break
    await new Promise(r => setTimeout(r, 200))
  }
  if (!vs) return {error: 'no v-select'}

  // dropdown 열기
  const opener = vs.querySelector('.vs__selected-options') || vs
  opener.click()
  await new Promise(r => setTimeout(r, 1200))

  // 모든 visible li 텍스트 dump
  const items = []
  for (const el of document.querySelectorAll('ul.vs__dropdown-menu li, .vs__dropdown-menu li, ul li, li')) {
    const t = (el.innerText || el.textContent || '').trim()
    const r = el.getBoundingClientRect()
    if (r.width === 0 || r.height === 0) continue
    if (t.length > 50 || !t) continue
    items.push({tag: el.tagName, cls: (typeof el.className === 'string' ? el.className : '').slice(0,100), text: t, role: el.getAttribute('role') || ''})
  }
  // 추가: dropdown 컨테이너 정보
  const dm = document.querySelector('.vs__dropdown-menu, ul[class*=dropdown], .v-select .open')
  return {items: items.slice(0, 40), dropdown_html: (dm ? dm.outerHTML.slice(0, 2000) : 'no-dropdown-menu')}
})()
"""


async def main():
    tab = find_tab()
    if not tab:
        print("no tab")
        return
    print(f"TAB: {tab['url']}")
    async with websockets.connect(tab["webSocketDebuggerUrl"], max_size=80_000_000) as ws:
        await ws.send(json.dumps({"id":1,"method":"Page.enable"}))
        await ws.send(json.dumps({"id":2,"method":"Runtime.evaluate","params":{"expression":JS,"returnByValue":True,"awaitPromise":True}}))
        end = asyncio.get_event_loop().time() + 30
        while asyncio.get_event_loop().time() < end:
            try: raw = await asyncio.wait_for(ws.recv(), timeout=3.0)
            except: continue
            e = json.loads(raw)
            if e.get("id") == 2:
                v = e["result"]["result"]["value"]
                print(json.dumps(v if isinstance(v, dict) else json.loads(v), ensure_ascii=False, indent=2))
                break


asyncio.run(main())
