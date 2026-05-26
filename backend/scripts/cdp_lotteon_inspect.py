"""LOTTEON 페이지 현재 상태 광범위 dump."""

import asyncio
import json
from urllib.request import urlopen
import websockets


def find_tab():
    for t in json.loads(urlopen("http://localhost:9223/json").read()):
        if t.get("type") == "page" and "lotteon.com" in t.get("url", "") and "2026052612069650" in t.get("url", ""):
            return t
    return None


JS = r"""
(() => {
  const out = {url: location.href, title: document.title, buttons: [], inputs: [], modals: []}
  for (const el of document.querySelectorAll('button, a[role=button], input[type=button], input[type=submit]')) {
    const t = (el.innerText || el.value || '').trim()
    const r = el.getBoundingClientRect()
    if (r.width === 0 || r.height === 0) continue
    out.buttons.push({text: t.slice(0,50), tag: el.tagName, cls: (typeof el.className === 'string' ? el.className : '').slice(0,120), disabled: el.disabled || false, x: Math.round(r.x), y: Math.round(r.y)})
  }
  for (const el of document.querySelectorAll('input[type=radio], input[type=checkbox]')) {
    const r = el.getBoundingClientRect()
    out.inputs.push({type: el.type, name: el.name, value: el.value, checked: el.checked, id: el.id, x: Math.round(r.x), y: Math.round(r.y)})
  }
  // 모달 후보 (z-index 높은 fixed)
  for (const el of document.querySelectorAll('div, section, dialog')) {
    const cs = getComputedStyle(el)
    const r = el.getBoundingClientRect()
    if ((cs.position === 'fixed' || cs.position === 'absolute') && parseInt(cs.zIndex||'0',10) >= 100 && r.height > 100 && el.offsetParent !== null) {
      out.modals.push({cls: (typeof el.className === 'string' ? el.className : '').slice(0,150), zIndex: cs.zIndex, h: Math.round(r.height), inner: (el.innerText||'').slice(0,400)})
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
                data = json.loads(v) if isinstance(v, str) else v
                print(json.dumps(data, ensure_ascii=False, indent=2))
                break


asyncio.run(main())
