"""LOTTEON 처음부터 — confirm/alert override + 자동 클릭 + POST 캡처."""

import asyncio
import json
from urllib.request import urlopen
import websockets


ORD = "2026052612069650"


def find_tab():
    for t in json.loads(urlopen("http://localhost:9223/json").read()):
        u = t.get("url", "")
        if t.get("type") == "page" and "lotteon.com" in u and ORD in u and "orderDetail" in u:
            return t
    return None


# 주문상세 페이지에 미리 inject (또는 새 페이지 navigate 후 재설치)
HOOK_AND_OVERRIDE_JS = r"""
(() => {
  // 1. confirm/alert override → 자동 true (취소 다이얼로그 막힘 차단)
  window.confirm = () => true
  window.alert = () => {}
  // 2. fetch/xhr hook
  if (window.__sambaLOTH) return 'already'
  window.__sambaLOTHlog = []
  const origFetch = window.fetch.bind(window)
  window.fetch = async function(input, init) {
    const url = typeof input === 'string' ? input : (input && input.url) || ''
    const method = (init && init.method) || 'GET'
    let body = ''
    try { if (init && init.body) body = typeof init.body === 'string' ? init.body : JSON.stringify(init.body) } catch(_) {}
    const e = {url, method, body: body.slice(0, 5000), ts: Date.now(), kind: 'fetch'}
    window.__sambaLOTHlog.push(e)
    try {
      const res = await origFetch(input, init)
      try { const cl = res.clone(); const t = await cl.text(); e.status = res.status; e.respBody = t.slice(0, 3500); } catch(_) {}
      return res
    } catch(er) { e.error = String(er); throw er; }
  }
  const oo = XMLHttpRequest.prototype.open, os = XMLHttpRequest.prototype.send
  XMLHttpRequest.prototype.open = function(m, u) { this.__m = m; this.__u = u; return oo.apply(this, arguments); }
  XMLHttpRequest.prototype.send = function(b) {
    const e = {url: this.__u, method: this.__m, body: (typeof b === 'string' ? b : '').slice(0, 5000), ts: Date.now(), kind: 'xhr'}
    window.__sambaLOTHlog.push(e)
    this.addEventListener('load', () => { try { e.status = this.status; e.respBody = (this.responseText || '').slice(0, 3500); } catch(_) {} })
    return os.apply(this, arguments)
  }
  window.__sambaLOTH = true
  return 'hooked-and-overridden'
})()
"""


CLICK_CANCEL_HAGI_JS = r"""
(() => {
  // 주문상세 → '취소하기' 버튼
  for (const el of document.querySelectorAll('button.btnAction')) {
    const t = (el.innerText || '').trim()
    if (t === '취소하기') {
      el.scrollIntoView({block:'center'})
      el.click()
      return 'clicked'
    }
  }
  return 'not-found'
})()
"""


# v-select 사유 선택
SELECT_REASON_JS = r"""
(() => {
  const vs = document.querySelector('div.v-select')
  if (!vs) return 'no-v-select'
  const opener = vs.querySelector('.vs__selected-options') || vs
  opener.click()
  return new Promise((resolve) => {
    setTimeout(() => {
      for (const el of document.querySelectorAll('.vs__dropdown-menu li, li[role=option], .vs__dropdown-option')) {
        const t = (el.innerText || '').trim()
        if (/구매\s*의사\s*없|구매의사없/.test(t)) {
          el.click()
          return resolve('selected:' + t)
        }
      }
      resolve('no-option')
    }, 600)
  })
})()
"""


CHECK_AGREE_JS = r"""
(() => {
  const ids = ['claimAgree','paymentAgree','checkbox_fnclTx','checkbox_indivisualInfoCollection','checkbox_indivisualInfoConsignment']
  const checked = []
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
      el.scrollIntoView({block:'center'})
      el.click()
      return 'clicked'
    }
  }
  return 'not-found'
})()
"""


DUMP = "JSON.stringify(window.__sambaLOTHlog || [])"


async def main():
    tab = find_tab()
    if not tab:
        print("no orderDetail tab")
        return
    print(f"TAB: {tab['url']}")
    async with websockets.connect(tab["webSocketDebuggerUrl"], max_size=80_000_000) as ws:
        mid = [0]
        async def call(m, p=None):
            mid[0] += 1
            await ws.send(json.dumps({"id": mid[0], "method": m, "params": p or {}}))
            end = asyncio.get_event_loop().time() + 20
            while asyncio.get_event_loop().time() < end:
                try: raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
                except asyncio.TimeoutError: continue
                e = json.loads(raw)
                if e.get("id") == mid[0]:
                    return e
            return {}
        async def evalJS(js, timeout=20):
            r = await call("Runtime.evaluate", {"expression": js, "returnByValue": True, "awaitPromise": True})
            return r.get("result", {}).get("result", {}).get("value")

        await call("Runtime.enable")
        await call("Page.enable")
        await call("Network.enable")

        # 1. cancellation 페이지 직접 navigate
        cancel_url = f"https://www.lotteon.com/p/order/claim/cancellation/orderCancellationAccept?odNo={ORD}&odSeq=1&procSeq=1"
        await call("Page.navigate", {"url": cancel_url})
        await asyncio.sleep(5)
        # 3. 새 페이지 (cancellation/orderCancellationAccept)에 hook 재설치
        print("HOOK2:", await evalJS(HOOK_AND_OVERRIDE_JS))
        # 4. 사유 선택
        print("REASON:", await evalJS(SELECT_REASON_JS))
        await asyncio.sleep(0.5)
        # 5. 동의 체크
        print("AGREE:", await evalJS(CHECK_AGREE_JS))
        await asyncio.sleep(0.3)
        # 6. 최종 클릭 (confirm override돼서 자동 true)
        print("FINAL:", await evalJS(CLICK_FINAL_JS))
        await asyncio.sleep(10)

        raw = await evalJS(DUMP)
        log = json.loads(raw) if isinstance(raw, str) else (raw or [])
        noise = ("google","facebook","doubleclick","analytics","kakao","naver.com","pinterest","twitter","hotjar","criteo","airbridge","tiktok","braze","cloudflare","/cdn-cgi/","creativecdn","datadoghq","static.lotteon","sdk.iad","asia.creativecdn","log.lotteon","bc.ad.daum","wcs.naver","lotteon.com/p/static","rec_collect","prd-analytics")
        cleaned = [e for e in log if not any(s in (e.get("url") or "").lower() for s in noise)]
        posts = [e for e in cleaned if e.get("method") in ("POST","PUT","DELETE","PATCH")]
        print("\n=== POSTS ===")
        print(json.dumps(posts, ensure_ascii=False, indent=2))
        print("\nFINAL_URL:", await evalJS("location.href"))


asyncio.run(main())
