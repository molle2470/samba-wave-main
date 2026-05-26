"""LOTTEON v-select 사유 선택 + final 클릭."""

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


OPEN_VSELECT_JS = r"""
(() => {
  const vs = document.querySelector('div.v-select')
  if (!vs) return 'no-v-select'
  // v-select 내부의 .vs__selected-options 또는 search input 클릭
  const opener = vs.querySelector('.vs__selected-options') || vs.querySelector('input') || vs
  opener.scrollIntoView({block:'center'})
  opener.click()
  return 'opened'
})()
"""


SELECT_OPTION_JS = r"""
(() => {
  // v-select 옵션은 보통 .vs__dropdown-menu > li
  for (const sel of ['.vs__dropdown-menu li', '.vs__dropdown-option', 'li[role=option]']) {
    for (const el of document.querySelectorAll(sel)) {
      const t = (el.innerText || '').trim()
      if (/구매\s*의사\s*없|구매의사없/.test(t)) {
        el.click()
        return 'selected:' + t
      }
    }
  }
  // 폴백 - 모든 visible li 중 매칭
  for (const el of document.querySelectorAll('li, a, button, div')) {
    const t = (el.innerText || '').trim()
    if (/^구매\s*의사\s*없/.test(t) && t.length < 30) {
      const r = el.getBoundingClientRect()
      if (r.width > 0) { el.click(); return 'fallback:' + t; }
    }
  }
  return 'no-option'
})()
"""


CHECK_AGREE_JS = r"""
(() => {
  const checked = []
  const ids = ['claimAgree','paymentAgree','checkbox_fnclTx','checkbox_indivisualInfoCollection','checkbox_indivisualInfoConsignment']
  for (const id of ids) {
    const el = document.getElementById(id)
    if (el && !el.checked) { el.click(); checked.push(id) }
  }
  return JSON.stringify(checked)
})()
"""


CLICK_FINAL_JS = r"""
(() => {
  for (const el of document.querySelectorAll('button')) {
    const t = (el.innerText || '').trim()
    if (el.disabled) continue
    if (t === '취소요청') {
      const r = el.getBoundingClientRect()
      el.scrollIntoView({block:'center'})
      el.click()
      return 'clicked @' + Math.round(r.x) + ',' + Math.round(r.y)
    }
  }
  return 'not-found'
})()
"""


HOOK_JS = r"""
(() => {
  if (window.__sambaLOT5) return 'already'
  window.__sambaLOT5log = []
  const origFetch = window.fetch.bind(window)
  window.fetch = async function(input, init) {
    const url = typeof input === 'string' ? input : (input && input.url) || ''
    const method = (init && init.method) || 'GET'
    let body = ''
    try { if (init && init.body) body = typeof init.body === 'string' ? init.body : JSON.stringify(init.body) } catch(_) {}
    const e = {url, method, body: body.slice(0, 5000), ts: Date.now(), kind: 'fetch'}
    window.__sambaLOT5log.push(e)
    try {
      const res = await origFetch(input, init)
      try { const cl = res.clone(); const t = await cl.text(); e.status = res.status; e.respBody = t.slice(0, 3000); } catch(_) {}
      return res
    } catch(er) { e.error = String(er); throw er; }
  }
  const oo = XMLHttpRequest.prototype.open, os = XMLHttpRequest.prototype.send
  XMLHttpRequest.prototype.open = function(m, u) { this.__m = m; this.__u = u; return oo.apply(this, arguments); }
  XMLHttpRequest.prototype.send = function(b) {
    const e = {url: this.__u, method: this.__m, body: (typeof b === 'string' ? b : '').slice(0, 5000), ts: Date.now(), kind: 'xhr'}
    window.__sambaLOT5log.push(e)
    this.addEventListener('load', () => { try { e.status = this.status; e.respBody = (this.responseText || '').slice(0, 3000); } catch(_) {} })
    return os.apply(this, arguments)
  }
  window.__sambaLOT5 = true
  return 'hooked'
})()
"""

DUMP = "JSON.stringify(window.__sambaLOT5log || [])"


async def main():
    tab = find_tab()
    if not tab:
        print("no tab")
        return
    async with websockets.connect(tab["webSocketDebuggerUrl"], max_size=80_000_000) as ws:
        mid = [0]
        async def call(m, p=None):
            mid[0] += 1
            await ws.send(json.dumps({"id": mid[0], "method": m, "params": p or {}}))
            while True:
                e = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
                if e.get("id") == mid[0]:
                    return e
        async def evalJS(js):
            r = await call("Runtime.evaluate", {"expression": js, "returnByValue": True, "awaitPromise": True})
            return r.get("result", {}).get("result", {}).get("value")
        async def native_click(x, y):
            await call("Input.dispatchMouseEvent", {"type":"mouseMoved","x":x,"y":y})
            await call("Input.dispatchMouseEvent", {"type":"mousePressed","x":x,"y":y,"button":"left","clickCount":1,"buttons":1})
            await call("Input.dispatchMouseEvent", {"type":"mouseReleased","x":x,"y":y,"button":"left","clickCount":1,"buttons":0})

        await call("Runtime.enable")
        await call("Network.enable")

        print("HOOK:", await evalJS(HOOK_JS))
        # v-select 좌표 native click (event 정확 트리거)
        await native_click(242, 390)  # selectSet 중앙
        await asyncio.sleep(0.5)
        # 다시 시도 — JS 클릭
        print("OPEN:", await evalJS(OPEN_VSELECT_JS))
        await asyncio.sleep(1.2)
        print("SELECT:", await evalJS(SELECT_OPTION_JS))
        await asyncio.sleep(0.7)
        print("AGREE:", await evalJS(CHECK_AGREE_JS))
        await asyncio.sleep(0.5)
        print("FINAL:", await evalJS(CLICK_FINAL_JS))
        await asyncio.sleep(8)

        raw = await evalJS(DUMP)
        log = json.loads(raw) if isinstance(raw, str) else (raw or [])
        noise = ("google", "facebook", "doubleclick", "analytics", "kakao", "naver.com", "pinterest", "twitter", "hotjar", "criteo", "airbridge", "tiktok", "braze", "cloudflare", "/cdn-cgi/", "creativecdn", "datadoghq", "static.lotteon", "sdk.iad", "asia.creativecdn", "log.lotteon", "bc.ad.daum", "wcs.naver", "lotteon.com/p/static", "rec_collect", "prd-analytics")
        cleaned = [e for e in log if not any(s in (e.get("url") or "").lower() for s in noise)]
        posts = [e for e in cleaned if e.get("method") in ("POST", "PUT", "DELETE", "PATCH")]
        print("\n=== POSTS ===")
        print(json.dumps(posts, ensure_ascii=False, indent=2))
        print("\nFINAL_URL:", await evalJS("location.href"))


asyncio.run(main())
