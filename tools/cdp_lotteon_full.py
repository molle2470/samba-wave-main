"""LOTTEON 송장 전체 추출 — 진단 스크립트의 안정 패턴(JS 내부 대기) 재사용.

배송상세조회 버튼 폴링 → 클릭 → dialog 폴링 → 택배사/송장 추출.
"""

import json
import sys
import time

import requests
import websocket

CDP = "http://localhost:9223"

FULL = r"""
(async () => {
  // 페이지 네비/로드 대기 — about:blank 레이스 방지
  const t0=Date.now();
  while(Date.now()-t0<25000){
    if(location.href.indexOf('lotteon.com')!==-1 && document.readyState!=='loading') break;
    await new Promise(r=>setTimeout(r,300));
  }
  const findBtn=()=>{ for(const b of document.querySelectorAll('button')){ if((b.textContent||'').trim().includes('배송상세조회'))return b; } for(const a of document.querySelectorAll('a')){ if((a.textContent||'').includes('배송상세조회'))return a; } return null; };
  let el=null; const tb=Date.now();
  while(Date.now()-tb<20000){ el=findBtn(); if(el)break; await new Promise(r=>setTimeout(r,400)); }
  if(!el) return {success:false, error:'배송상세조회 버튼 없음', finalUrl:location.href, len:(document.body?.innerText||'').length};
  el.click();
  let dialog=null; const td=Date.now();
  while(Date.now()-td<8000){ dialog=document.querySelector('dialog[open], [role="dialog"]'); if(dialog)break; await new Promise(r=>setTimeout(r,300)); }
  if(!dialog) return {success:false, error:'dialog 미열림', finalUrl:location.href};
  await new Promise(r=>setTimeout(r,1500));
  const field=(label)=>{ for(const e of dialog.querySelectorAll('*')){ if((e.textContent||'').trim()===label && e.children.length===0){ const s=e.nextElementSibling; if(!s)continue; const lk=s.querySelector('a')||(s.tagName==='A'?s:null); return (lk?.textContent||s.textContent||'').trim(); } } return ''; };
  let courierName=field('택배사'); let trackingNumber=field('송장번호');
  if(!trackingNumber){ for(const lk of dialog.querySelectorAll('a[href*="tracking"], a[href*="InvNo"]')){ const t=lk.textContent.trim(); if(/^\d{8,}$/.test(t)){trackingNumber=t;break;} } }
  return {success:!!trackingNumber, courierName, trackingNumber, finalUrl:location.href, dialogText:(dialog.innerText||'').slice(0,300)};
})()
"""


def main():
    url = sys.argv[1]
    r = requests.put(f"{CDP}/json/new?{url}", timeout=10)
    tab = r.json()
    tid, ws_url = tab["id"], tab["webSocketDebuggerUrl"]
    ws = websocket.create_connection(ws_url, max_size=None, timeout=70)
    try:
        ws.send(json.dumps({"id": 1, "method": "Page.enable"}))
        time.sleep(0.2)
        ws.send(json.dumps({"id": 3, "method": "Page.bringToFront"}))
        time.sleep(0.2)
        ws.send(json.dumps({"id": 4, "method": "Runtime.enable"}))
        time.sleep(0.3)
        ws.send(json.dumps({"id": 2, "method": "Runtime.evaluate", "params": {
            "expression": FULL, "returnByValue": True, "awaitPromise": True, "timeout": 65000}}))
        end = time.time() + 70
        while time.time() < end:
            ws.settimeout(max(1, end - time.time()))
            try:
                msg = json.loads(ws.recv())
            except Exception:
                continue
            if msg.get("id") == 2:
                if "result" in msg:
                    print(json.dumps(msg["result"].get("result", {}).get("value"), ensure_ascii=False))
                else:
                    print(f"ERR {json.dumps(msg)[:300]}")
                break
    finally:
        ws.close()
        try:
            requests.get(f"{CDP}/json/close/{tid}", timeout=5)
        except Exception:
            pass


if __name__ == "__main__":
    main()
