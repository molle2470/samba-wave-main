"""LOTTEON cancel_js 직접 evaluate 테스트 — Playwright 환경 외부에서.

웨일 탭으로 cancellation 페이지 navigate → cancel_js evaluate → 결과.
"""

import asyncio
import json
import sys
from urllib.request import urlopen
import websockets


# 새 주문 (성희 계정, 발주됨, 배송전)
TEST_ORD = sys.argv[1] if len(sys.argv) > 1 else "2026052511787646"


def find_tab():
    for t in json.loads(urlopen("http://localhost:9223/json").read()):
        u = t.get("url", "")
        if t.get("type") == "page" and "lotteon.com" in u:
            return t
    return None


# daemon.py 의 LOTTEON_CANCEL_JS 와 동일
CANCEL_JS = r"""
(async () => {
  try { window.confirm = () => true; window.alert = () => {}; } catch(_) {}

  let vs = null
  for (let i = 0; i < 40; i++) {
    vs = document.querySelector('div.v-select')
    if (vs) break
    await new Promise(r => setTimeout(r, 200))
  }
  if (!vs) return {success: false, error: 'v-select 사유 dropdown 미발견'}

  const opener = vs.querySelector('.vs__selected-options') || vs
  opener.dispatchEvent(new MouseEvent('mousedown', {bubbles: true, button: 0}))
  opener.dispatchEvent(new MouseEvent('mouseup', {bubbles: true, button: 0}))
  opener.click()
  await new Promise(r => setTimeout(r, 800))

  let selected = false
  for (const el of document.querySelectorAll('ul.vs__dropdown-menu li[role=option], .vs__dropdown-option')) {
    const t = (el.innerText || el.textContent || '').trim()
    if (t === '구매 의사 없어짐' || t === '구매의사 없어짐') {
      el.dispatchEvent(new MouseEvent('mousedown', {bubbles: true, button: 0}))
      el.dispatchEvent(new MouseEvent('mouseup', {bubbles: true, button: 0}))
      el.click(); selected = true; break
    }
  }
  if (!selected) return {success: false, error: '사유 옵션(구매 의사 없어짐) 미발견'}
  await new Promise(r => setTimeout(r, 500))

  const agreeIds = ['claimAgree','paymentAgree','checkbox_fnclTx','checkbox_indivisualInfoCollection','checkbox_indivisualInfoConsignment']
  for (const id of agreeIds) {
    const el = document.getElementById(id)
    if (el && !el.checked) el.click()
  }
  await new Promise(r => setTimeout(r, 500))

  let cancelResp = null
  const origFetch = window.fetch.bind(window)
  window.fetch = async function(input, init) {
    const url = typeof input === 'string' ? input : (input && input.url) || ''
    const res = await origFetch(input, init)
    if (/processOrderCancellation/.test(url)) {
      try { const cl = res.clone(); cancelResp = JSON.parse(await cl.text()) } catch(_) {}
    }
    return res
  }
  const _xhrOpen = XMLHttpRequest.prototype.open
  const _xhrSend = XMLHttpRequest.prototype.send
  XMLHttpRequest.prototype.open = function(method, url) { this.__sambaURL = url; return _xhrOpen.apply(this, arguments) }
  XMLHttpRequest.prototype.send = function(body) {
    this.addEventListener('load', () => {
      try {
        if (/processOrderCancellation/.test(this.__sambaURL || '')) {
          cancelResp = JSON.parse(this.responseText)
        }
      } catch(_) {}
    })
    return _xhrSend.apply(this, arguments)
  }

  let clicked = false
  for (const el of document.querySelectorAll('button')) {
    const t = (el.innerText || '').trim()
    if (t === '취소요청' && !el.disabled) { el.click(); clicked = true; break }
  }
  if (!clicked) return {success: false, error: '취소요청 버튼 미발견'}

  const start = Date.now()
  while (Date.now() - start < 20000) {
    if (cancelResp !== null) break
    await new Promise(r => setTimeout(r, 300))
  }
  if (!cancelResp) return {success: false, error: 'processOrderCancellation 응답 timeout'}

  const code = (cancelResp.returnCode || cancelResp.code || '').toString()
  const msg = cancelResp.message || cancelResp.msg || ''
  const ok = code === '200' || /SUCCESS/i.test(msg)
  return {
    success: ok, cancelled: ok,
    alreadyShipped: /이미\s*발송|이미\s*취소|배송\s*시작/.test(msg),
    reason: ok ? 'LOTTEON 발주취소 완료' : (msg || `returnCode=${code}`),
    response: cancelResp,
  }
})()
"""


async def main():
    tab = find_tab()
    if not tab:
        print("no LOTTEON tab — 웨일에 lotteon.com 탭 열어주세요")
        return
    print(f"TAB: {tab['url']}")
    cancel_url = f"https://www.lotteon.com/p/order/claim/cancellation/orderCancellationAccept?odNo={TEST_ORD}&odSeq=1&procSeq=1"

    async with websockets.connect(tab["webSocketDebuggerUrl"], max_size=80_000_000) as ws:
        mid = [0]
        responses = {}
        dialog_events = []

        async def pump():
            try:
                async for raw in ws:
                    e = json.loads(raw)
                    if "id" in e:
                        responses[e["id"]] = e
                    elif e.get("method") == "Page.javascriptDialogOpening":
                        dialog_events.append(e)
                        print(f"DIALOG: {e.get('params', {}).get('message','')[:80]} → auto accept")
                        # immediate accept
                        mid[0] += 1
                        await ws.send(json.dumps({"id": mid[0], "method": "Page.handleJavaScriptDialog", "params": {"accept": True}}))
            except Exception:
                pass

        pump_task = asyncio.create_task(pump())

        async def call(m, p=None, timeout=30):
            mid[0] += 1
            our_id = mid[0]
            await ws.send(json.dumps({"id": our_id, "method": m, "params": p or {}}))
            end = asyncio.get_event_loop().time() + timeout
            while asyncio.get_event_loop().time() < end:
                if our_id in responses:
                    return responses.pop(our_id)
                await asyncio.sleep(0.05)
            return {}

        async def evalJS(js, timeout=40):
            r = await call("Runtime.evaluate", {"expression": js, "returnByValue": True, "awaitPromise": True}, timeout=timeout)
            return r.get("result", {}).get("result", {}).get("value")

        await call("Page.enable")
        await call("Runtime.enable")

        # 1. cancellation 페이지 navigate
        await call("Page.navigate", {"url": cancel_url})
        await asyncio.sleep(6)

        # 2. cancel_js evaluate
        result = await evalJS(CANCEL_JS)
        print(f"\n=== cancel_js RESULT (dialogs seen: {len(dialog_events)}) ===")
        print(json.dumps(result, ensure_ascii=False, indent=2))

        pump_task.cancel()


asyncio.run(main())
