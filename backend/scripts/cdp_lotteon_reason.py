"""LOTTEON 사유 dropdown 정확한 selector 찾기."""

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


JS = r"""
(() => {
  const out = {reason_candidates: [], dropdowns: [], texts_with_reason: []}
  // '구매 의사' 텍스트 들어간 모든 element
  for (const el of document.querySelectorAll('*')) {
    const t = (el.innerText || el.textContent || '').trim()
    if (/구매\s*의사/.test(t) && t.length < 30) {
      const r = el.getBoundingClientRect()
      if (r.width > 0 && r.height > 0) {
        out.texts_with_reason.push({tag: el.tagName, cls: (typeof el.className === 'string' ? el.className : '').slice(0,80), text: t, x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width)})
      }
    }
  }
  // dropdown 후보 — 텍스트 안 펼친 상태도 포함
  for (const el of document.querySelectorAll('button, div[class*=select], div[class*=Select], div[class*=dropdown], div[class*=Dropdown]')) {
    const t = (el.innerText || '').trim()
    const r = el.getBoundingClientRect()
    if (r.width > 100 && r.height > 30 && r.width < 600) {
      if (/사유|선택|구매|^클레임|^반품|^취소/.test(t) || el.className.includes('select')) {
        out.reason_candidates.push({tag: el.tagName, cls: (typeof el.className === 'string' ? el.className : '').slice(0,100), text: t.slice(0,60), x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width)})
      }
    }
  }
  return JSON.stringify(out)
})()
"""


async def main():
    tab = find_tab()
    if not tab:
        print("no tab")
        return
    async with websockets.connect(tab["webSocketDebuggerUrl"], max_size=80_000_000) as ws:
        await ws.send(json.dumps({"id":1,"method":"Runtime.evaluate","params":{"expression":JS,"returnByValue":True}}))
        while True:
            e = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
            if e.get("id") == 1:
                v = e["result"]["result"]["value"]
                print(json.dumps(json.loads(v) if isinstance(v, str) else v, ensure_ascii=False, indent=2))
                break


asyncio.run(main())
