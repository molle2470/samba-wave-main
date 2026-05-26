"""송장 수집 가능성 검증 — 실제 확장앱 ensureLoggedIn 로그인 + 스크랩.

사용: python cdp_tracking_verify.py <siteKey> <accountId> <scrapeMode> <trackingUrl>
  siteKey: ssg|lotteon|abcmart|musinsa  (확장앱 AUTO_LOGIN_SITES 키)
  accountId: sa_xxx
  scrapeMode: ssg|abc|lotteon|musinsa  (cdp_tracking_probe.SCRAPE_JS 키)
  trackingUrl: 송장조회 URL

1) SAMBA-WAVE 백그라운드 SW 에서 ensureLoggedIn(siteKey,{accountId}) 실행 → 진짜 로그인
2) 새 탭으로 trackingUrl 열고 스크랩 JS 실행 → 송장 추출
"""

import json
import sys
import time

import requests
import websocket

from cdp_tracking_probe import SCRAPE_JS

CDP = "http://localhost:9223"
EXT_ID = "ojfcneljbbajgcmpmklgglhenieehicb"  # SAMBA-WAVE


def get_sw_ws():
    for t in requests.get(f"{CDP}/json", timeout=10).json():
        if t.get("type") == "service_worker" and EXT_ID in t.get("url", ""):
            return t.get("webSocketDebuggerUrl")
    return None


def evaluate(ws_url, expr, timeout=120):
    ws = websocket.create_connection(ws_url, max_size=None, timeout=timeout + 5)
    try:
        ws.send(json.dumps({"id": 1, "method": "Runtime.enable"}))
        time.sleep(0.3)
        ws.send(json.dumps({"id": 2, "method": "Runtime.evaluate", "params": {
            "expression": expr, "returnByValue": True, "awaitPromise": True,
            "timeout": timeout * 1000}}))
        end = time.time() + timeout
        while time.time() < end:
            ws.settimeout(max(1, end - time.time()))
            try:
                msg = json.loads(ws.recv())
            except Exception:
                continue
            if msg.get("id") == 2:
                if "result" in msg:
                    return msg["result"].get("result", {}).get("value")
                return f"ERR: {json.dumps(msg)[:300]}"
        return "TIMEOUT"
    finally:
        ws.close()


def main():
    site_key, account_id, scrape_mode, url = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]

    sw = get_sw_ws()
    if not sw:
        print("SAMBA-WAVE SW 못 찾음")
        return

    # 1) 실제 확장앱 로그인
    login_expr = (
        f"(async()=>{{try{{const ok=await globalThis.ensureLoggedIn("
        f"{json.dumps(site_key)},{{accountId:{json.dumps(account_id)}}});"
        f"return {{loginOk:!!ok}};}}catch(e){{return {{loginOk:false,err:String(e&&e.message||e)}};}}}})()"
    )
    print(f"[로그인] ensureLoggedIn('{site_key}', acc={account_id}) ...")
    lr = evaluate(sw, login_expr, timeout=120)
    print(f"[로그인결과] {json.dumps(lr, ensure_ascii=False)}")

    time.sleep(2)

    # 2) 새 탭 + 스크랩
    r = requests.put(f"{CDP}/json/new?{url}", timeout=10)
    tab = r.json()
    target_id = tab["id"]
    ws_url = tab["webSocketDebuggerUrl"]
    print(f"[탭] {url}")
    time.sleep(6)
    try:
        sr = evaluate(ws_url, SCRAPE_JS[scrape_mode], timeout=70)
        print(f"[스크랩결과] {json.dumps(sr, ensure_ascii=False)}")
    finally:
        try:
            requests.get(f"{CDP}/json/close/{target_id}", timeout=5)
        except Exception:
            pass


if __name__ == "__main__":
    main()
