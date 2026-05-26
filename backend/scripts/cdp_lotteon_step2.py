"""LOTTEON 취소접수 페이지에서 hook 재설치 → 동의 자동 체크 → 취소요청 클릭 → POST 캡처."""

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


HOOK_JS = r"""
(() => {
  if (window.__sambaLOT3) return 'already'
  window.__sambaLOT3log = []
  const origFetch = window.fetch.bind(window)
  window.fetch = async function(input, init) {
    const url = typeof input === 'string' ? input : (input && input.url) || ''
    const method = (init && init.method) || 'GET'
    let body = ''
    let hdrs = {}
    try {
      if (init && init.body) body = typeof init.body === 'string' ? init.body : JSON.stringify(init.body)
      if (init && init.headers) {
        if (init.headers instanceof Headers) init.headers.forEach((v,k) => hdrs[k] = v)
        else hdrs = init.headers
      }
    } catch(_) {}
    const entry = {url, method, body: body.slice(0, 4000), headers: hdrs, ts: Date.now(), kind: 'fetch'}
    window.__sambaLOT3log.push(entry)
    try {
      const res = await origFetch(input, init)
      try { const cl = res.clone(); const t = await cl.text(); entry.status = res.status; entry.respBody = t.slice(0, 3000); } catch(_) {}
      return res
    } catch(e) { entry.error = String(e); throw e; }
  }
  const oo = XMLHttpRequest.prototype.open, os = XMLHttpRequest.prototype.send
  XMLHttpRequest.prototype.open = function(m, u) { this.__m = m; this.__u = u; return oo.apply(this, arguments); }
  XMLHttpRequest.prototype.send = function(b) {
    const e = {url: this.__u, method: this.__m, body: (typeof b === 'string' ? b : '').slice(0, 4000), ts: Date.now(), kind: 'xhr'}
    window.__sambaLOT3log.push(e)
    this.addEventListener('load', () => { try { e.status = this.status; e.respBody = (this.responseText || '').slice(0, 3000); } catch(_) {} })
    return os.apply(this, arguments)
  }
  window.__sambaLOT3 = true
  return 'hooked'
})()
"""


CHECK_ALL_AGREE_JS = r"""
(() => {
  const checked = []
  // 다양한 동의 체크박스 자동 체크
  const ids = ['claimAgree', 'paymentAgree', 'checkbox_fnclTx', 'checkbox_indivisualInfoCollection', 'checkbox_indivisualInfoConsignment']
  for (const id of ids) {
    const el = document.getElementById(id)
    if (el && !el.checked) {
      el.click()
      checked.push(id)
    }
  }
  // label click fallback (라벨 클릭으로 hidden checkbox 토글되는 경우)
  for (const el of document.querySelectorAll('label')) {
    const t = (el.innerText || '').trim()
    if (/주문\s*취소.*결제.*서비스.*동의|구매.*결제\s*서비스/.test(t)) {
      const cb = el.querySelector('input[type=checkbox]') || document.getElementById(el.getAttribute('for') || '')
      if (cb && !cb.checked) {
        cb.click()
        checked.push('label:' + t.slice(0,30))
      }
    }
  }
  return JSON.stringify(checked)
})()
"""


CLICK_FINAL_JS = r"""
(() => {
  for (const el of document.querySelectorAll('button')) {
    const t = (el.innerText || '').trim()
    if (el.disabled) continue
    if (t === '취소요청' || t === '취소 요청') {
      const r = el.getBoundingClientRect()
      el.scrollIntoView({block:'center'})
      el.click()
      return 'clicked:' + t + ' @' + Math.round(r.x) + ',' + Math.round(r.y)
    }
  }
  return 'not-found'
})()
"""


PAGE_STATE_JS = r"""
(() => ({url: location.href, title: document.title}))()
"""


DUMP = "JSON.stringify(window.__sambaLOT3log || [])"


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

        print("PAGE:", await evalJS(PAGE_STATE_JS))
        print("HOOK:", await evalJS(HOOK_JS))
        print("AGREE:", await evalJS(CHECK_ALL_AGREE_JS))
        await asyncio.sleep(1)
        print("FINAL:", await evalJS(CLICK_FINAL_JS))
        await asyncio.sleep(8)

        raw = await evalJS(DUMP)
        log = json.loads(raw) if isinstance(raw, str) else (raw or [])
        noise = ("google", "facebook", "doubleclick", "analytics", "kakao", "naver.com", "pinterest", "twitter", "hotjar", "criteo", "airbridge", "tiktok", "braze", "cloudflare", "/cdn-cgi/", "creativecdn", "datadoghq", "static.lotteon", "sdk.iad", "asia.creativecdn", "log.lotteon", "bc.ad.daum", "wcs.naver", "lotteon.com/p/static", "rec_collect", "prd-analytics")
        cleaned = [e for e in log if not any(s in (e.get("url") or "").lower() for s in noise)]
        posts = [e for e in cleaned if e.get("method") in ("POST", "PUT", "DELETE", "PATCH")]
        print("\n=== CLEAN LOTTEON CALLS ===")
        print(json.dumps(cleaned, ensure_ascii=False, indent=2))
        print("\n=== POST/PUT ===")
        print(json.dumps(posts, ensure_ascii=False, indent=2))
        print("\nFINAL_URL:", await evalJS("location.href"))


asyncio.run(main())
