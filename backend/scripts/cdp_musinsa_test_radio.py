"""테스트: '취소 요청' 클릭 + 단순변심 라디오 클릭까지만. 최종 확정 클릭 절대 X.

좌표 native mouse event (CDP Input.dispatchMouseEvent) — Radix UI React 핸들러 정상 트리거.
"""

import asyncio
import json
import sys
import time
from urllib.request import Request, urlopen
import websockets


ORDER_NO = "202605252219370003"
DETAIL_URL = f"https://www.musinsa.com/order/order-detail/{ORDER_NO}"


def list_tabs():
    return [t for t in json.loads(urlopen("http://localhost:9223/json").read()) if t.get("type") == "page"]


def find_tab_url(substr: str):
    for t in list_tabs():
        if substr in t.get("url", ""):
            return t
    return None


def open_url(url: str):
    req = Request(f"http://localhost:9223/json/new?{url}", method="PUT")
    return json.loads(urlopen(req).read())


class CDP:
    def __init__(self, ws):
        self.ws = ws
        self.id = 0
        self.responses = {}
        self.events = []
        self._task = asyncio.create_task(self._pump())

    async def _pump(self):
        try:
            async for raw in self.ws:
                e = json.loads(raw)
                if "id" in e:
                    self.responses[e["id"]] = e
                else:
                    self.events.append(e)
        except Exception:
            pass

    async def call(self, method, params=None, timeout=20.0):
        self.id += 1
        mid = self.id
        await self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        loop = asyncio.get_event_loop()
        end = loop.time() + timeout
        while loop.time() < end:
            if mid in self.responses:
                return self.responses.pop(mid)
            await asyncio.sleep(0.03)
        raise TimeoutError(method)

    async def evalJS(self, js, timeout=20.0):
        r = await self.call("Runtime.evaluate", {"expression": js, "returnByValue": True, "awaitPromise": True}, timeout=timeout)
        return r.get("result", {}).get("result", {}).get("value")

    async def native_click(self, x: float, y: float):
        # 좌표 ‘진짜’ 마우스 클릭 (React Radix UI 핸들러 트리거)
        await self.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
        await self.call("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1, "buttons": 1})
        await self.call("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1, "buttons": 0})


HOOK_JS = r"""
(() => {
  if (window.__sambaHook4) return 'already';
  window.__sambaLog4 = [];
  const origFetch = window.fetch.bind(window);
  window.fetch = async function(input, init) {
    const url = typeof input === 'string' ? input : (input && input.url) || '';
    const method = (init && init.method) || (input && input.method) || 'GET';
    let body = ''; let headers = {};
    try {
      if (init && init.body) body = typeof init.body === 'string' ? init.body : JSON.stringify(init.body);
      if (init && init.headers) { if (init.headers instanceof Headers) init.headers.forEach((v,k)=>headers[k]=v); else headers = init.headers; }
    } catch(_) {}
    const entry = {url, method, body: body.slice(0, 4000), headers, ts: Date.now(), kind: 'fetch'};
    window.__sambaLog4.push(entry);
    try { const res = await origFetch(input, init); try { const cl = res.clone(); const t = await cl.text(); entry.status = res.status; entry.respBody = t.slice(0, 3000); } catch(_) {} return res; }
    catch(e) { entry.error = String(e); throw e; }
  };
  const oo = XMLHttpRequest.prototype.open, os = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function(m, u) { this.__m = m; this.__u = u; return oo.apply(this, arguments); };
  XMLHttpRequest.prototype.send = function(b) {
    const e = {url: this.__u, method: this.__m, body: (typeof b === 'string' ? b : '').slice(0, 4000), ts: Date.now(), kind: 'xhr'};
    window.__sambaLog4.push(e);
    this.addEventListener('load', () => { try { e.status = this.status; e.respBody = (this.responseText || '').slice(0, 3000); } catch(_) {} });
    return os.apply(this, arguments);
  };
  window.__sambaHook4 = true;
  return 'hooked';
})()
"""


FIND_CANCEL_BTN_RECT_JS = r"""
(() => {
  for (const el of document.querySelectorAll('button')) {
    const t = (el.innerText || '').trim();
    const r = el.getBoundingClientRect();
    if (r.width === 0 || el.disabled) continue;
    if (t.length > 12) continue;
    if (/취소.*요청|주문.*취소|^취소$/.test(t)) {
      return JSON.stringify({text: t, x: r.x + r.width/2, y: r.y + r.height/2, w: r.width, h: r.height});
    }
  }
  return JSON.stringify({error: 'not-found'});
})()
"""


FIND_RADIO_RECT_JS = r"""
(() => {
  // Radix UI button#claimReasonCode-1
  const el = document.querySelector('#claimReasonCode-1');
  if (!el) return JSON.stringify({error: 'no-radio'});
  const r = el.getBoundingClientRect();
  return JSON.stringify({
    id: el.id, ariaChecked: el.getAttribute('aria-checked'),
    x: r.x + r.width/2, y: r.y + r.height/2, w: r.width, h: r.height,
  });
})()
"""


CHECK_RADIO_STATE_JS = r"""
(() => {
  const el = document.querySelector('#claimReasonCode-1');
  if (!el) return JSON.stringify({error: 'no-radio'});
  return JSON.stringify({
    ariaChecked: el.getAttribute('aria-checked'),
    dataState: el.getAttribute('data-state'),
  });
})()
"""


PAGE_TITLE_JS = "JSON.stringify({url: location.href, title: document.title})"

DUMP_LOG = "JSON.stringify(window.__sambaLog4 || [])"


async def main():
    # 1. 주문 탭 진입 (기존 있으면 reuse, 없으면 새 탭)
    tab = find_tab_url(ORDER_NO)
    if not tab:
        nt = open_url(DETAIL_URL)
        ws_url = nt["webSocketDebuggerUrl"]
        print("OPENED NEW TAB")
        await asyncio.sleep(5)
    else:
        ws_url = tab["webSocketDebuggerUrl"]
        print(f"REUSE TAB: {tab['url']}")

    async with websockets.connect(ws_url, max_size=80_000_000) as ws:
        cdp = CDP(ws)
        await cdp.call("Runtime.enable")
        await cdp.call("Page.enable")
        await cdp.call("Network.enable")

        print("HOOK1:", await cdp.evalJS(HOOK_JS))
        print("PAGE:", await cdp.evalJS(PAGE_TITLE_JS))

        # 2. '취소 요청' 또는 '주문 취소' 버튼 좌표 native click
        raw = await cdp.evalJS(FIND_CANCEL_BTN_RECT_JS)
        info = json.loads(raw)
        print("CANCEL_BTN:", info)
        if "error" in info:
            print("ABORT — cancel button not found")
            return
        await cdp.native_click(info["x"], info["y"])
        print(f"NATIVE_CLICK @{info['x']},{info['y']}")
        await asyncio.sleep(5)

        # 3. 새 페이지(claim) — hook 재설치
        print("PAGE2:", await cdp.evalJS(PAGE_TITLE_JS))
        print("HOOK2:", await cdp.evalJS(HOOK_JS))

        # 4. 라디오 좌표 native click
        raw = await cdp.evalJS(FIND_RADIO_RECT_JS)
        info = json.loads(raw)
        print("RADIO_BEFORE:", info)
        if "error" in info:
            print("ABORT — radio not found")
            return
        await cdp.native_click(info["x"], info["y"])
        print(f"RADIO_CLICK @{info['x']},{info['y']}")
        await asyncio.sleep(1.0)

        # 5. 상태 재확인
        raw = await cdp.evalJS(CHECK_RADIO_STATE_JS)
        print("RADIO_AFTER:", raw)

        # 최종 클릭 절대 안 함 — 종료
        print("\n=== STOP ===  최종 '취소 요청하기' 버튼 누르지 않음.")
        print("=== XHR LOG ===")
        raw = await cdp.evalJS(DUMP_LOG)
        log = json.loads(raw) if isinstance(raw, str) else (raw or [])
        noise = ("google", "facebook", "doubleclick", "analytics", "kakao", "naver.com/wcs", "pinterest", "twitter", "hotjar", "criteo", "airbridge", "tiktok", "braze", "cloudflare", "/cdn-cgi/", "/log/", "static.msscdn", "snippet.maze", "creativecdn", "datadoghq", "capi.madup", "data.musinsa.com", "rum")
        cleaned = [e for e in log if not any(s in (e.get("url") or "").lower() for s in noise)]
        print(json.dumps(cleaned, ensure_ascii=False, indent=2))


asyncio.run(main())
