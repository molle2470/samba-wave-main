"""로그인 페이지 input/button 셀렉터 덤프 — 데몬 로그인 설정용 (추측 금지)."""

import json
import sys
import time

import requests
import websocket

CDP = "http://localhost:9223"

INSPECT = r"""
(async () => {
  await new Promise(r=>setTimeout(r,8000));  // SPA 렌더 대기
  const inputs = Array.from(document.querySelectorAll('input')).slice(0,30).map(i=>({
    id:i.id||'', name:i.name||'', type:i.type||'', placeholder:i.placeholder||'',
    cls:(i.className||'').slice(0,40), autocomplete:i.autocomplete||''
  }));
  const btns = Array.from(document.querySelectorAll('button, input[type=submit], a[role=button]')).slice(0,25).map(b=>({
    tag:b.tagName, id:b.id||'', type:b.type||'', txt:(b.textContent||b.value||'').trim().slice(0,20),
    cls:(b.className||'').slice(0,40)
  }));
  return {title:document.title, url:location.href, inputs, btns};
})()
"""


def run(url):
    tab = requests.put(f"{CDP}/json/new?{url}", timeout=10).json()
    tid, ws_url = tab["id"], tab["webSocketDebuggerUrl"]
    ws = websocket.create_connection(ws_url, max_size=None, timeout=40)
    try:
        ws.send(json.dumps({"id": 1, "method": "Page.enable"}))
        time.sleep(0.2)
        ws.send(json.dumps({"id": 3, "method": "Page.bringToFront"}))
        time.sleep(0.2)
        ws.send(json.dumps({"id": 4, "method": "Runtime.enable"}))
        time.sleep(0.3)
        ws.send(json.dumps({"id": 2, "method": "Runtime.evaluate", "params": {
            "expression": INSPECT, "returnByValue": True, "awaitPromise": True, "timeout": 20000}}))
        end = time.time() + 25
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
    print(f"##### {sys.argv[1]}")
    run(sys.argv[1])
