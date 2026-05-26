"""로그인 페이지 셀렉터 덤프 (incognito 컨텍스트 — 쿠키 없어 로그인폼 강제 노출)."""

import json
import sys
import time

import requests
import websocket

CDP = "http://localhost:9223"

INSPECT = r"""
(async () => {
  await new Promise(r=>setTimeout(r,9000));
  const inputs = Array.from(document.querySelectorAll('input')).slice(0,30).map(i=>({
    id:i.id||'', name:i.name||'', type:i.type||'', placeholder:i.placeholder||'',
    cls:(i.className||'').slice(0,40), ac:i.autocomplete||''
  })).filter(i=>i.type!=='hidden');
  const btns = Array.from(document.querySelectorAll('button, input[type=submit], a[role=button]')).slice(0,30).map(b=>({
    tag:b.tagName, id:b.id||'', type:b.type||'', txt:(b.textContent||b.value||'').trim().slice(0,20),
    cls:(b.className||'').slice(0,45)
  })).filter(b=>b.txt||b.id||b.type==='submit');
  return {title:document.title, url:location.href, inputs, btns};
})()
"""


def browser_ws():
    return requests.get(f"{CDP}/json/version", timeout=10).json()["webSocketDebuggerUrl"]


def bsend(ws, mid, method, params=None):
    ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))


def bwait(ws, mid, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        ws.settimeout(max(1, end - time.time()))
        try:
            m = json.loads(ws.recv())
        except Exception:
            continue
        if m.get("id") == mid:
            return m
    return None


def run(url):
    bws = websocket.create_connection(browser_ws(), max_size=None, timeout=40)
    ctx_id = None
    try:
        r = bwait(bws, 1, 10) if not bsend(bws, 1, "Target.createBrowserContext") else None
        ctx_id = r["result"]["browserContextId"]
        bsend(bws, 2, "Target.createTarget", {"url": url, "browserContextId": ctx_id})
        tr = bwait(bws, 2, 10)
        target_id = tr["result"]["targetId"]
        # 해당 타깃 ws 찾기
        time.sleep(1)
        ws_url = None
        for t in requests.get(f"{CDP}/json", timeout=10).json():
            if t.get("id") == target_id:
                ws_url = t.get("webSocketDebuggerUrl")
                break
        if not ws_url:
            print("타깃 ws 못 찾음")
            return
        ws = websocket.create_connection(ws_url, max_size=None, timeout=40)
        try:
            ws.send(json.dumps({"id": 10, "method": "Runtime.enable"}))
            time.sleep(0.3)
            ws.send(json.dumps({"id": 11, "method": "Runtime.evaluate", "params": {
                "expression": INSPECT, "returnByValue": True, "awaitPromise": True, "timeout": 20000}}))
            end = time.time() + 25
            while time.time() < end:
                ws.settimeout(max(1, end - time.time()))
                try:
                    m = json.loads(ws.recv())
                except Exception:
                    continue
                if m.get("id") == 11:
                    print(json.dumps(m.get("result", {}).get("result", {}).get("value"), ensure_ascii=False, indent=2))
                    break
        finally:
            ws.close()
    finally:
        if ctx_id:
            bsend(bws, 99, "Target.disposeBrowserContext", {"browserContextId": ctx_id})
            bwait(bws, 99, 5)
        bws.close()


if __name__ == "__main__":
    print(f"##### {sys.argv[1]}")
    run(sys.argv[1])
