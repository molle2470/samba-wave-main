"""확인 모달의 '주문 취소 요청하기' 클릭 + POST 캡처."""

import asyncio
import json
from urllib.request import urlopen
import websockets


def find_tab():
    for t in json.loads(urlopen("http://localhost:9223/json").read()):
        if t.get("type") != "page":
            continue
        u = t.get("url", "")
        if "musinsa.com/order/claim/order-cancel" in u and "202605260834580001" in u:
            return t
    return None


CLICK_CONFIRM_JS = r"""
(() => {
  // 모달의 '주문 취소 요청하기' (확정 버튼)
  for (const el of document.querySelectorAll('button')) {
    const t = (el.innerText || '').trim();
    if (el.disabled) continue;
    if (t === '주문 취소 요청하기' || t === '주문취소 요청하기' || /^주문.*취소.*요청하기$/.test(t)) {
      const r = el.getBoundingClientRect();
      el.scrollIntoView({block:'center'});
      el.click();
      return 'confirm-clicked:' + t + ' @' + Math.round(r.x) + ',' + Math.round(r.y);
    }
  }
  return 'confirm-not-found';
})()
"""

DUMP_ALL = "JSON.stringify({log1: window.__sambaLog || [], log2: window.__sambaLog2 || [], log3: window.__sambaLog3 || [], url: location.href})"


async def main():
    tab = find_tab()
    if not tab:
        print(json.dumps({"error": "no tab"}))
        return
    print("TAB:", tab["url"])
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
        await call("Page.enable")

        print("CLICK:", await evalJS(CLICK_CONFIRM_JS))
        await asyncio.sleep(8.0)

        raw = await evalJS(DUMP_ALL)
        data = json.loads(raw) if isinstance(raw, str) else raw
        noise = ("google", "facebook", "doubleclick", "analytics", "kakao", "naver.com/wcs", "pinterest", "twitter", "hotjar", "criteo", "airbridge", "tiktok", "braze", "cloudflare", "/cdn-cgi/", "/log/", "static.msscdn", "snippet.maze", "creativecdn", "datadoghq", "capi.madup", "data.musinsa.com", "rum")
        combined = (data.get("log1") or []) + (data.get("log2") or []) + (data.get("log3") or [])
        seen = set()
        dedup = []
        for e in combined:
            k = (e.get("url"), e.get("ts"))
            if k in seen: continue
            seen.add(k)
            dedup.append(e)
        cleaned = [e for e in dedup if not any(s in (e.get("url") or "").lower() for s in noise)]
        posts = [e for e in cleaned if e.get("method") in ("POST", "PUT", "DELETE", "PATCH")]
        print("\n=== POST/PUT/DELETE ===")
        print(json.dumps(posts, ensure_ascii=False, indent=2))
        print("\n=== FINAL URL ===", data.get("url"))


asyncio.run(main())
