"""LOTTEON 끝까지 cancel — '취소하기' → 모달 → '취소요청' → POST 캡처."""

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
  if (window.__sambaLotteonHook2) return 'already'
  window.__sambaLotteonLog2 = []
  const origFetch = window.fetch.bind(window)
  window.fetch = async function(input, init) {
    const url = typeof input === 'string' ? input : (input && input.url) || ''
    const method = (init && init.method) || 'GET'
    let body = ''
    try { if (init && init.body) body = typeof init.body === 'string' ? init.body : JSON.stringify(init.body) } catch(_) {}
    const entry = {url, method, body: body.slice(0, 4000), ts: Date.now(), kind: 'fetch'}
    window.__sambaLotteonLog2.push(entry)
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
    window.__sambaLotteonLog2.push(e)
    this.addEventListener('load', () => { try { e.status = this.status; e.respBody = (this.responseText || '').slice(0, 3000); } catch(_) {} })
    return os.apply(this, arguments)
  }
  window.__sambaLotteonHook2 = true
  return 'hooked'
})()
"""


CLICK_CANCEL_BTN_JS = r"""
(() => {
  // 메인 페이지의 '취소하기' 버튼 (모달 오픈 트리거)
  for (const el of document.querySelectorAll('button')) {
    const t = (el.innerText || '').trim()
    if (t === '취소하기' && el.classList.contains('btnAction')) {
      el.scrollIntoView({block:'center'})
      el.click()
      return 'opened-modal:' + t
    }
  }
  return 'not-found'
})()
"""


SELECT_REASON_JS = r"""
(() => {
  // dropdown 또는 select - '구매 의사 없음' 선택. 또는 라디오.
  // 1) select element
  for (const sel of document.querySelectorAll('select')) {
    for (const opt of sel.options) {
      if (/구매\s*의사|단순/.test(opt.textContent || '')) {
        sel.value = opt.value
        sel.dispatchEvent(new Event('change', {bubbles:true}))
        return 'select-changed:' + opt.textContent.trim()
      }
    }
  }
  // 2) dropdown 토글 + 옵션 click (custom)
  // 모달의 dropdown 아이콘 click 후 옵션 click
  return 'no-select-found'
})()
"""


CHECK_AGREE_JS = r"""
(() => {
  // '주문취소, 결제 서비스 이용 동의' 체크박스 자동 체크
  for (const cb of document.querySelectorAll('input[type=checkbox]')) {
    const lbl = (cb.closest('label')?.innerText || cb.parentElement?.innerText || '').trim()
    if (/동의/.test(lbl) && !cb.checked) {
      cb.click()
      return 'checked-agree:' + lbl.slice(0,40)
    }
  }
  return 'no-agree-cb'
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


DUMP = "JSON.stringify(window.__sambaLotteonLog2 || [])"


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
        async def native_click(x, y):
            await call("Input.dispatchMouseEvent", {"type":"mouseMoved","x":x,"y":y})
            await call("Input.dispatchMouseEvent", {"type":"mousePressed","x":x,"y":y,"button":"left","clickCount":1,"buttons":1})
            await call("Input.dispatchMouseEvent", {"type":"mouseReleased","x":x,"y":y,"button":"left","clickCount":1,"buttons":0})

        await call("Runtime.enable")
        await call("Network.enable")

        print("HOOK:", await evalJS(HOOK_JS))
        print("OPEN_MODAL:", await evalJS(CLICK_CANCEL_BTN_JS))
        await asyncio.sleep(3)
        print("REASON:", await evalJS(SELECT_REASON_JS))
        await asyncio.sleep(0.5)
        print("AGREE:", await evalJS(CHECK_AGREE_JS))
        await asyncio.sleep(0.5)
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


asyncio.run(main())
