"""확장앱 타깃 중 SAMBA-WAVE 식별 + ensureLoggedIn 존재 확인."""

import json
import time

import requests
import websocket

CDP = "http://localhost:9223"


def probe(ws_url):
    try:
        ws = websocket.create_connection(ws_url, max_size=None, timeout=8)
    except Exception as e:
        return f"connect fail: {e}"
    try:
        ws.send(json.dumps({"id": 1, "method": "Runtime.enable"}))
        time.sleep(0.3)
        expr = (
            "JSON.stringify({name:(chrome?.runtime?.getManifest?.()||{}).name||'?',"
            "ver:(chrome?.runtime?.getManifest?.()||{}).version||'?',"
            "hasEnsure:(typeof globalThis.ensureLoggedIn==='function'),"
            "hasSourcingQueue:(typeof globalThis.SambaBackgroundCore!=='undefined')})"
        )
        ws.send(json.dumps({"id": 2, "method": "Runtime.evaluate",
                            "params": {"expression": expr, "returnByValue": True, "awaitPromise": True}}))
        end = time.time() + 8
        while time.time() < end:
            ws.settimeout(max(1, end - time.time()))
            try:
                msg = json.loads(ws.recv())
            except Exception:
                continue
            if msg.get("id") == 2:
                return msg.get("result", {}).get("result", {}).get("value", str(msg)[:200])
        return "no reply"
    finally:
        ws.close()


def main():
    targets = requests.get(f"{CDP}/json", timeout=10).json()
    for t in targets:
        if t.get("type") not in ("service_worker", "background_page"):
            continue
        url = t.get("url", "")
        if "chrome-extension://" not in url:
            continue
        ws_url = t.get("webSocketDebuggerUrl")
        if not ws_url:
            continue
        ext_id = url.split("chrome-extension://")[1].split("/")[0]
        print(f"\n[{ext_id}] {url[:60]}")
        print("  ", probe(ws_url))


if __name__ == "__main__":
    main()
