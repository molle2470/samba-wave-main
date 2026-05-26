"""LOTTEON 송장 페이지 상태 진단 — giftBoxDetail vs orderDetail 비교."""

import json
import sys
import time

import requests
import websocket

CDP = "http://localhost:9223"

DIAG = r"""
(async () => {
  await new Promise(r=>setTimeout(r,12000));
  const txt=(document.body?.innerText||'');
  const btns=Array.from(document.querySelectorAll('button')).map(b=>(b.textContent||'').trim()).filter(Boolean).slice(0,40);
  const ifr=Array.from(document.querySelectorAll('iframe')).map(f=>f.src||f.getAttribute('src')||'(no-src)');
  return {
    title:document.title,
    finalUrl:location.href,
    innerLen:txt.length,
    has배송:/배송/.test(txt),
    has택배:/택배/.test(txt),
    has송장:/송장/.test(txt),
    has배송상세조회:/배송상세조회/.test(txt),
    hasNotFound:/(주문\s*정보|찾을\s*수\s*없|조회된\s*주문)/.test(txt),
    iframeCount:ifr.length,
    iframes:ifr.slice(0,5),
    buttons:btns,
    bodyMid:txt.slice(300,900),
  };
})()
"""


def run(url):
    r = requests.put(f"{CDP}/json/new?{url}", timeout=10)
    tab = r.json()
    tid, ws_url = tab["id"], tab["webSocketDebuggerUrl"]
    ws = websocket.create_connection(ws_url, max_size=None, timeout=40)
    try:
        ws.send(json.dumps({"id": 1, "method": "Runtime.enable"}))
        time.sleep(0.3)
        ws.send(json.dumps({"id": 3, "method": "Page.enable"}))
        time.sleep(0.2)
        ws.send(json.dumps({"id": 4, "method": "Page.bringToFront"}))
        time.sleep(0.2)
        ws.send(json.dumps({"id": 2, "method": "Runtime.evaluate", "params": {
            "expression": DIAG, "returnByValue": True, "awaitPromise": True, "timeout": 30000}}))
        end = time.time() + 30
        while time.time() < end:
            ws.settimeout(max(1, end - time.time()))
            try:
                msg = json.loads(ws.recv())
            except Exception:
                continue
            if msg.get("id") == 2:
                print(json.dumps(msg.get("result", {}).get("result", {}).get("value"), ensure_ascii=False, indent=2))
                break
    finally:
        ws.close()
        try:
            requests.get(f"{CDP}/json/close/{tid}", timeout=5)
        except Exception:
            pass


if __name__ == "__main__":
    od = sys.argv[1]
    print(f"\n##### {od}")
    run(od)
