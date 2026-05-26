"""주문 끝까지 취소 + CDP Network 도메인으로 POST cancel 캡처.

순서:
1. 주문상세 탭 진입 + Network.enable
2. native click '취소 요청'
3. navigate /order/claim/order-cancel/...
4. native click 단순변심 (Radix radio)
5. native click '취소 요청하기 (1개)'
6. 확인 모달 native click '주문 취소 요청하기'
7. complete 페이지 도달 → Network event 전체 dump
"""

import asyncio
import json
from urllib.request import Request, urlopen
import websockets


ORDER_NO = "202605260858590004"
DETAIL_URL = f"https://www.musinsa.com/order/order-detail/{ORDER_NO}"


def list_tabs():
    return [t for t in json.loads(urlopen("http://localhost:9223/json").read()) if t.get("type") == "page"]


def find_tab(substr):
    for t in list_tabs():
        if substr in t.get("url", ""):
            return t
    return None


def open_url(url):
    req = Request(f"http://localhost:9223/json/new?{url}", method="PUT")
    return json.loads(urlopen(req).read())


class CDP:
    def __init__(self, ws):
        self.ws = ws; self.id = 0; self.responses = {}; self.events = []
        self.req_map = {}  # requestId → {url, method, postData, headers}
        self.resp_map = {}  # requestId → {status, mimeType, body}
        self._task = asyncio.create_task(self._pump())

    async def _pump(self):
        try:
            async for raw in self.ws:
                e = json.loads(raw)
                if "id" in e:
                    self.responses[e["id"]] = e
                else:
                    m = e.get("method", "")
                    p = e.get("params", {})
                    if m == "Network.requestWillBeSent":
                        req = p.get("request", {})
                        self.req_map[p["requestId"]] = {
                            "url": req.get("url", ""),
                            "method": req.get("method", ""),
                            "postData": req.get("postData", ""),
                            "headers": req.get("headers", {}),
                        }
                    elif m == "Network.responseReceived":
                        rid = p.get("requestId")
                        if rid in self.req_map:
                            r = p.get("response", {})
                            self.resp_map[rid] = {"status": r.get("status"), "mimeType": r.get("mimeType")}
                    elif m == "Network.loadingFinished":
                        # body fetch
                        rid = p.get("requestId")
                        if rid in self.req_map and self.req_map[rid]["method"] in ("POST", "PUT", "DELETE", "PATCH"):
                            self.events.append({"type": "finished", "requestId": rid})
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

    async def native_click(self, x, y):
        await self.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
        await self.call("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1, "buttons": 1})
        await self.call("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1, "buttons": 0})

    async def fetch_body(self, request_id):
        try:
            r = await self.call("Network.getResponseBody", {"requestId": request_id}, timeout=5)
            return r.get("result", {})
        except Exception as e:
            return {"error": str(e)}


FIND_CANCEL_BTN_JS = r"""
(() => {
  for (const el of document.querySelectorAll('button')) {
    const t = (el.innerText || '').trim();
    const r = el.getBoundingClientRect();
    if (r.width === 0 || el.disabled || t.length > 12) continue;
    if (/취소.*요청|주문.*취소|^취소$/.test(t)) {
      return JSON.stringify({text: t, x: r.x + r.width/2, y: r.y + r.height/2});
    }
  }
  return JSON.stringify({error: 'no-cancel-btn'});
})()
"""


FIND_RADIO_JS = r"""
(() => {
  const el = document.querySelector('#claimReasonCode-1');
  if (!el) return JSON.stringify({error: 'no-radio'});
  const r = el.getBoundingClientRect();
  return JSON.stringify({x: r.x + r.width/2, y: r.y + r.height/2, ariaChecked: el.getAttribute('aria-checked')});
})()
"""


FIND_FINAL_JS = r"""
(() => {
  for (const el of document.querySelectorAll('button')) {
    const t = (el.innerText || '').trim();
    if (el.disabled) continue;
    if (/^취소 요청하기/.test(t) && t.length < 30) {
      const r = el.getBoundingClientRect();
      return JSON.stringify({text: t, x: r.x + r.width/2, y: r.y + r.height/2});
    }
  }
  return JSON.stringify({error: 'no-final'});
})()
"""


FIND_CONFIRM_JS = r"""
(() => {
  for (const el of document.querySelectorAll('button')) {
    const t = (el.innerText || '').trim();
    if (el.disabled) continue;
    if (t === '주문 취소 요청하기' || /^주문.*취소.*요청하기$/.test(t)) {
      const r = el.getBoundingClientRect();
      return JSON.stringify({text: t, x: r.x + r.width/2, y: r.y + r.height/2});
    }
  }
  return JSON.stringify({error: 'no-confirm'});
})()
"""


PAGE_URL_JS = "location.href"


async def main():
    tab = find_tab(ORDER_NO)
    if not tab:
        # 또 다른 무신사 order-detail 탭 reuse 안 — 새 탭
        nt = open_url(DETAIL_URL)
        ws_url = nt["webSocketDebuggerUrl"]
        print("OPENED NEW TAB")
        await asyncio.sleep(5)
    else:
        ws_url = tab["webSocketDebuggerUrl"]
        print(f"REUSE TAB: {tab['url']}")

    async with websockets.connect(ws_url, max_size=80_000_000) as ws:
        cdp = CDP(ws)
        await cdp.call("Network.enable")
        await cdp.call("Page.enable")
        await cdp.call("Runtime.enable")

        print("URL:", await cdp.evalJS(PAGE_URL_JS))

        # 1. 취소 요청 클릭
        raw = await cdp.evalJS(FIND_CANCEL_BTN_JS)
        info = json.loads(raw)
        print("CANCEL_BTN:", info)
        if "error" in info:
            return
        await cdp.native_click(info["x"], info["y"])
        await asyncio.sleep(5)
        print("URL2:", await cdp.evalJS(PAGE_URL_JS))

        # 2. 사유 라디오 클릭
        raw = await cdp.evalJS(FIND_RADIO_JS)
        info = json.loads(raw)
        print("RADIO:", info)
        if "error" in info:
            return
        await cdp.native_click(info["x"], info["y"])
        await asyncio.sleep(1.0)

        # 3. final 클릭
        raw = await cdp.evalJS(FIND_FINAL_JS)
        info = json.loads(raw)
        print("FINAL:", info)
        if "error" in info:
            return
        await cdp.native_click(info["x"], info["y"])
        await asyncio.sleep(2.5)

        # 4. 확인 모달 클릭
        raw = await cdp.evalJS(FIND_CONFIRM_JS)
        info = json.loads(raw)
        print("CONFIRM:", info)
        if "error" not in info:
            await cdp.native_click(info["x"], info["y"])
        await asyncio.sleep(8.0)

        print("URL_FINAL:", await cdp.evalJS(PAGE_URL_JS))

        # 5. Network req map dump
        print("\n=== CANCEL-RELATED REQUESTS ===")
        results = []
        for rid, req in cdp.req_map.items():
            url = req["url"]
            if not any(k in url.lower() for k in ("claim", "cancel", "order-items", "refund", "calculator", "/api2/")):
                continue
            if any(n in url.lower() for n in ("google", "facebook", "analytics", "static.msscdn", "datadoghq")):
                continue
            resp = cdp.resp_map.get(rid, {})
            body_info = {}
            if req["method"] in ("POST", "PUT", "DELETE", "PATCH"):
                body_info = await cdp.fetch_body(rid)
            results.append({
                "url": url, "method": req["method"],
                "postData": req["postData"][:2000],
                "status": resp.get("status"),
                "respBody": (body_info.get("body") or "")[:2500] if body_info else "",
            })
        # POST/PUT/DELETE 먼저
        posts = [r for r in results if r["method"] in ("POST", "PUT", "DELETE", "PATCH")]
        gets = [r for r in results if r["method"] == "GET"]
        print("--- POST/PUT/DELETE ---")
        print(json.dumps(posts, ensure_ascii=False, indent=2))
        print("\n--- GET (top 10) ---")
        print(json.dumps(gets[:10], ensure_ascii=False, indent=2))


asyncio.run(main())
