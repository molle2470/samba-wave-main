"""LOTTEON 주문 취소 - 최종 '취소요청' 클릭 시 POST 캡처."""

import asyncio
import json
from urllib.request import urlopen
import websockets


ORD = "2026052612069650"


def find_tab():
    for t in json.loads(urlopen("http://localhost:9223/json").read()):
        if t.get("type") == "page" and "lotteon.com" in t.get("url", "") and ORD in t.get("url", ""):
            return t
    return None


HOOK_JS = r"""
(() => {
  if (window.__sambaLotteonHook) return 'already'
  window.__sambaLotteonLog = []
  const origFetch = window.fetch.bind(window)
  window.fetch = async function(input, init) {
    const url = typeof input === 'string' ? input : (input && input.url) || ''
    const method = (init && init.method) || (input && input.method) || 'GET'
    let body = ''
    let headers = {}
    try {
      if (init && init.body) body = typeof init.body === 'string' ? init.body : JSON.stringify(init.body)
      if (init && init.headers) {
        if (init.headers instanceof Headers) init.headers.forEach((v,k) => headers[k] = v)
        else headers = init.headers
      }
    } catch(_) {}
    const entry = {url, method, body: body.slice(0, 3500), headers, ts: Date.now(), kind: 'fetch'}
    window.__sambaLotteonLog.push(entry)
    try {
      const res = await origFetch(input, init)
      try { const cl = res.clone(); const t = await cl.text(); entry.status = res.status; entry.respBody = t.slice(0, 2500); } catch(_) {}
      return res
    } catch(e) { entry.error = String(e); throw e; }
  }
  const oo = XMLHttpRequest.prototype.open, os = XMLHttpRequest.prototype.send
  XMLHttpRequest.prototype.open = function(m, u) { this.__m = m; this.__u = u; return oo.apply(this, arguments); }
  XMLHttpRequest.prototype.send = function(b) {
    const e = {url: this.__u, method: this.__m, body: (typeof b === 'string' ? b : '').slice(0, 3500), ts: Date.now(), kind: 'xhr'}
    window.__sambaLotteonLog.push(e)
    this.addEventListener('load', () => { try { e.status = this.status; e.respBody = (this.responseText || '').slice(0, 2500); } catch(_) {} })
    return os.apply(this, arguments)
  }
  window.__sambaLotteonHook = true
  return 'hooked'
})()
"""


# 모달의 '취소요청' 빨간 버튼 클릭
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


DUMP = "JSON.stringify(window.__sambaLotteonLog || [])"


async def main():
    tab = find_tab()
    if not tab:
        print("no LOTTEON tab")
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
        print("CLICK:", await evalJS(CLICK_FINAL_JS))
        await asyncio.sleep(7)

        raw = await evalJS(DUMP)
        log = json.loads(raw) if isinstance(raw, str) else (raw or [])
        noise = ("google", "facebook", "doubleclick", "analytics", "kakao", "naver.com", "pinterest", "twitter", "hotjar", "criteo", "airbridge", "tiktok", "braze", "cloudflare", "/cdn-cgi/", "creativecdn", "datadoghq", "static.lotteon", "sdk.iad", "asia.creativecdn", "log.lotteon", "bc.ad.daum", "wcs.naver", "lotteon.com/p/static")
        cleaned = [e for e in log if not any(s in (e.get("url") or "").lower() for s in noise)]
        posts = [e for e in cleaned if e.get("method") in ("POST", "PUT", "DELETE", "PATCH")]
        print("\n=== ALL LOTTEON CALLS ===")
        print(json.dumps(cleaned, ensure_ascii=False, indent=2))
        print("\n=== POST/PUT ===")
        print(json.dumps(posts, ensure_ascii=False, indent=2))
        final_url = await evalJS("location.href")
        print("\nFINAL URL:", final_url)


asyncio.run(main())
