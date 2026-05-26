"""MUSINSA 송장 2단계 추출 — 클릭 후 trace 네비게이션 넘어 재부착 스크랩.

확장앱: 로그인은 ensureLoggedIn, 페이지 흐름은 order-detail → 배송조회 클릭 → trace.
raw CDP 는 네비로 컨텍스트 죽으므로 단계 분리 (Playwright 는 page 객체로 자동 처리됨).
사용: python cdp_musinsa_full.py <accountId> <orderNo>
"""

import json
import sys
import time

import requests
import websocket

CDP = "http://localhost:9223"
EXT_ID = "ojfcneljbbajgcmpmklgglhenieehicb"

CLICK_JS = r"""
(async () => {
  const t0=Date.now();
  const isWrong=()=>{ const t=(document.body?.innerText||'').slice(0,4000); return /주문\s*정보를?\s*찾을\s*수\s*없|잘못된\s*접근/.test(t); };
  let btn=null;
  while(Date.now()-t0<15000){
    if(isWrong()) return {clicked:false, error:'wrong_account'};
    const bs=Array.from(document.querySelectorAll('button'));
    // 정확 텍스트 '배송조회'/'배송 조회' 우선 (헤더 nav 오클릭 방지)
    btn=bs.find(b=>{ const t=(b.textContent||'').replace(/\s+/g,'').trim(); return t==='배송조회'; });
    if(btn && !btn.disabled) break;
    await new Promise(r=>setTimeout(r,300));
  }
  if(!btn) return {clicked:false, error:'배송조회 버튼(정확매칭) 없음', len:(document.body?.innerText||'').length,
    cands:Array.from(document.querySelectorAll('button')).map(b=>(b.textContent||'').replace(/\s+/g,'').trim()).filter(t=>t.includes('배송')).slice(0,10)};
  const clickedText=(btn.textContent||'').replace(/\s+/g,'').trim();
  btn.click();
  return {clicked:true, clickedText};
})()
"""

SCRAPE_TRACE_JS = r"""
(async () => {
  const t0=Date.now(); let ce=null;
  while(Date.now()-t0<20000){ ce=document.querySelector('p.company-name'); if(ce&&(ce.textContent||'').trim())break; await new Promise(r=>setTimeout(r,300)); }
  if(!ce) return {success:false, error:'company-name 미로드', finalUrl:location.href, path:location.pathname};
  const courierName=ce.textContent.trim();
  const te=document.querySelector('button.tracking-number');
  const trackingNumber=(te?.textContent||'').trim();
  return {success:!!trackingNumber, courierName, trackingNumber, finalUrl:location.href};
})()
"""


def sw_ws():
    for t in requests.get(f"{CDP}/json", timeout=10).json():
        if t.get("type") == "service_worker" and EXT_ID in t.get("url", ""):
            return t["webSocketDebuggerUrl"]
    return None


def evaluate(ws_url, expr, timeout=40):
    ws = websocket.create_connection(ws_url, max_size=None, timeout=timeout + 5)
    try:
        ws.send(json.dumps({"id": 1, "method": "Runtime.enable"}))
        time.sleep(0.3)
        ws.send(json.dumps({"id": 2, "method": "Runtime.evaluate", "params": {
            "expression": expr, "returnByValue": True, "awaitPromise": True, "timeout": timeout * 1000}}))
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
                return f"ERR:{json.dumps(msg.get('error'))}"
        return "TIMEOUT"
    finally:
        ws.close()


def tab_ws(tid):
    for t in requests.get(f"{CDP}/json", timeout=10).json():
        if t.get("id") == tid:
            return t.get("webSocketDebuggerUrl"), t.get("url", "")
    return None, ""


def main():
    account_id, order_no = sys.argv[1], sys.argv[2]
    url = f"https://www.musinsa.com/order/order-detail/{order_no}"

    # 1) 로그인
    sw = sw_ws()
    login_expr = (f"(async()=>{{try{{const ok=await globalThis.ensureLoggedIn('musinsa',"
                  f"{{accountId:{json.dumps(account_id)}}});return {{loginOk:!!ok}};}}"
                  f"catch(e){{return {{loginOk:false,err:String(e&&e.message||e)}};}}}})()")
    print(f"[로그인] {evaluate(sw, login_expr, 120)}")
    time.sleep(2)

    # 2) order-detail 탭 생성
    tab = requests.put(f"{CDP}/json/new?{url}", timeout=10).json()
    tid = tab["id"]
    print(f"[탭] {url}")
    time.sleep(7)
    ws_url, _ = tab_ws(tid)
    # bringToFront
    try:
        w = websocket.create_connection(ws_url, timeout=10)
        w.send(json.dumps({"id": 9, "method": "Page.bringToFront"}))
        time.sleep(0.3)
        w.close()
    except Exception:
        pass

    # 3) 배송조회 클릭 (네비 발생 가능 — 에러 무시)
    cr = evaluate(ws_url, CLICK_JS, 25)
    print(f"[클릭] {json.dumps(cr, ensure_ascii=False)}")

    # 4) trace 페이지 진입 폴링 (탭 URL 변화 감지)
    end = time.time() + 25
    final_url = ""
    while time.time() < end:
        time.sleep(1)
        _, cur = tab_ws(tid)
        final_url = cur
        if "/order-service/my/delivery/trace" in cur:
            break

    # 5) trace 페이지 재부착 스크랩
    ws_url2, cur = tab_ws(tid)
    print(f"[현재URL] {cur}")
    sr = evaluate(ws_url2, SCRAPE_TRACE_JS, 30)
    print(f"[스크랩] {json.dumps(sr, ensure_ascii=False)}")

    try:
        requests.get(f"{CDP}/json/close/{tid}", timeout=5)
    except Exception:
        pass


if __name__ == "__main__":
    main()
