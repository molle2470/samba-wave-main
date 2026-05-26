"""LOTTEON step3 — 사유 dropdown 열기 + '구매의사 없어짐' 선택 + final 클릭."""

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


# 사유 dropdown 열기 — btnToggle 클릭
OPEN_REASON_JS = r"""
(() => {
  const toggles = document.querySelectorAll('button.btnToggle')
  for (const t of toggles) {
    const r = t.getBoundingClientRect()
    if (r.width > 0 && r.height > 0) {
      t.click()
      return 'toggle-clicked @' + Math.round(r.x) + ',' + Math.round(r.y)
    }
  }
  return 'no-toggle'
})()
"""


# dropdown 옵션 중 '구매 의사 없어짐' 클릭 (li, a, button, span 등)
SELECT_REASON_JS = r"""
(() => {
  // 열린 dropdown 안에서 '구매 의사 없어짐' 텍스트 매칭
  for (const el of document.querySelectorAll('li, a, button, span, div')) {
    const t = (el.innerText || '').trim()
    if (t === '구매 의사 없어짐' || t === '구매의사 없어짐') {
      const r = el.getBoundingClientRect()
      if (r.width > 0 && r.height > 0) {
        el.click()
        return 'selected:' + t + ' @' + Math.round(r.x) + ',' + Math.round(r.y)
      }
    }
  }
  return 'no-option'
})()
"""


# dropdown 모든 옵션 dump (디버그용)
DUMP_OPTIONS_JS = r"""
(() => {
  const out = []
  for (const el of document.querySelectorAll('ul, ol, .dropdown, .selectList, .layerSelect, div[class*=Dropdown], div[class*=dropdown]')) {
    const items = el.querySelectorAll('li, a, button, span')
    const lst = []
    for (const it of items) {
      const t = (it.innerText || '').trim()
      const r = it.getBoundingClientRect()
      if (t && r.width > 0 && r.height > 0 && t.length < 50) lst.push({t, tag: it.tagName, x: Math.round(r.x), y: Math.round(r.y)})
    }
    if (lst.length > 1 && lst.length < 30) out.push({container: (typeof el.className === 'string' ? el.className : '').slice(0,80), items: lst})
  }
  return JSON.stringify(out.slice(0, 5))
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
  if (window.__sambaLOT4) return 'already'
  window.__sambaLOT4log = []
  const origFetch = window.fetch.bind(window)
  window.fetch = async function(input, init) {
    const url = typeof input === 'string' ? input : (input && input.url) || ''
    const method = (init && init.method) || 'GET'
    let body = ''
    try { if (init && init.body) body = typeof init.body === 'string' ? init.body : JSON.stringify(init.body) } catch(_) {}
    const e = {url, method, body: body.slice(0, 4000), ts: Date.now(), kind: 'fetch'}
    window.__sambaLOT4log.push(e)
    try {
      const res = await origFetch(input, init)
      try { const cl = res.clone(); const t = await cl.text(); e.status = res.status; e.respBody = t.slice(0, 3000); } catch(_) {}
      return res
    } catch(er) { e.error = String(er); throw er; }
  }
  const oo = XMLHttpRequest.prototype.open, os = XMLHttpRequest.prototype.send
  XMLHttpRequest.prototype.open = function(m, u) { this.__m = m; this.__u = u; return oo.apply(this, arguments); }
  XMLHttpRequest.prototype.send = function(b) {
    const e = {url: this.__u, method: this.__m, body: (typeof b === 'string' ? b : '').slice(0, 4000), ts: Date.now(), kind: 'xhr'}
    window.__sambaLOT4log.push(e)
    this.addEventListener('load', () => { try { e.status = this.status; e.respBody = (this.responseText || '').slice(0, 3000); } catch(_) {} })
    return os.apply(this, arguments)
  }
  window.__sambaLOT4 = true
  return 'hooked'
})()
"""

DUMP = "JSON.stringify(window.__sambaLOT4log || [])"


async def main():
    tab = find_tab()
    if not tab:
        print("no tab")
        return
    print(f"TAB: {tab['url']}")
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

        await call("Runtime.enable")
        await call("Network.enable")

        print("HOOK:", await evalJS(HOOK_JS))
        print("OPEN_REASON:", await evalJS(OPEN_REASON_JS))
        await asyncio.sleep(1)
        print("OPTIONS:", await evalJS(DUMP_OPTIONS_JS))
        print("SELECT:", await evalJS(SELECT_REASON_JS))
        await asyncio.sleep(1)
        print("FINAL:", await evalJS(CLICK_FINAL_JS))
        await asyncio.sleep(8)
        # 추가 alert/confirm 모달
        print("FINAL2:", await evalJS(CLICK_FINAL_JS))
        await asyncio.sleep(5)

        raw = await evalJS(DUMP)
        log = json.loads(raw) if isinstance(raw, str) else (raw or [])
        noise = ("google", "facebook", "doubleclick", "analytics", "kakao", "naver.com", "pinterest", "twitter", "hotjar", "criteo", "airbridge", "tiktok", "braze", "cloudflare", "/cdn-cgi/", "creativecdn", "datadoghq", "static.lotteon", "sdk.iad", "asia.creativecdn", "log.lotteon", "bc.ad.daum", "wcs.naver", "lotteon.com/p/static", "rec_collect", "prd-analytics")
        cleaned = [e for e in log if not any(s in (e.get("url") or "").lower() for s in noise)]
        posts = [e for e in cleaned if e.get("method") in ("POST", "PUT", "DELETE", "PATCH")]
        print("\n=== POSTS ===")
        print(json.dumps(posts, ensure_ascii=False, indent=2))
        print("\nFINAL_URL:", await evalJS("location.href"))


asyncio.run(main())
