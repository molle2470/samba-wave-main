"""확장앱 실제 방식 재현 — SW의 chrome.tabs.create + executeScript 로 스크랩.

CDP 부착 탭(webdriver 플래그)이 아닌, 확장앱이 만드는 일반 탭에서 스크랩.
사용: python cdp_ext_tab_scrape.py <mode> <url>
mode: ssg|abc|lotteon
"""

import json
import sys
import time

import requests
import websocket

CDP = "http://localhost:9223"
EXT_ID = "ojfcneljbbajgcmpmklgglhenieehicb"

# 페이지 MAIN world 에서 실행될 스크랩 함수 (executeScript func 직렬화).
SCRAPE_FUNC = {
    "ssg": r"""
async function(){
  const t0=Date.now(); let c=null;
  while(Date.now()-t0<15000){ c=document.querySelector('.tx_state em'); if(c)break; await new Promise(r=>setTimeout(r,300)); }
  if(!c) return {success:false,error:'no .tx_state em',finalUrl:location.href,len:(document.body?.innerText||'').length};
  const courierName=(c.querySelector('span')?.textContent||'').trim();
  let trackingNumber=''; for(const n of c.childNodes){ if(n.nodeType===3){ const m=n.textContent.match(/\d{8,}/); if(m){trackingNumber=m[0];break;} } }
  return {success:!!trackingNumber,courierName,trackingNumber,finalUrl:location.href};
}
""",
    "abc": r"""
async function(){
  const t0=Date.now(); let c=null;
  while(Date.now()-t0<15000){ c=document.querySelector('div.status-info .info-desc'); if(c&&(c.textContent||'').trim())break; await new Promise(r=>setTimeout(r,300)); }
  if(!c) return {success:false,error:'no status-info',finalUrl:location.href,len:(document.body?.innerText||'').length};
  const courierName=c.textContent.trim();
  const te=document.querySelector('div.status-info .info-link'); const raw=(te?.textContent||'').trim(); const m=raw.match(/\d{8,}/);
  const trackingNumber=m?m[0]:raw;
  return {success:!!trackingNumber,courierName,trackingNumber,finalUrl:location.href};
}
""",
    "lotteon": r"""
async function(){
  const findBtn=()=>{ for(const b of document.querySelectorAll('button')){ const t=(b.textContent||'').trim(); if(t.includes('배송상세조회'))return b; } for(const a of document.querySelectorAll('a')){ if((a.textContent||'').includes('배송상세조회'))return a; } return null; };
  let el=null; const tb=Date.now();
  while(Date.now()-tb<20000){ el=findBtn(); if(el)break; await new Promise(r=>setTimeout(r,400)); }
  if(!el) return {success:false,error:'배송상세조회 버튼 없음',finalUrl:location.href,len:(document.body?.innerText||'').length,head:(document.body?.innerText||'').slice(0,200)};
  el.click();
  let dialog=null; const td=Date.now();
  while(Date.now()-td<8000){ dialog=document.querySelector('dialog[open], [role="dialog"]'); if(dialog)break; await new Promise(r=>setTimeout(r,300)); }
  if(!dialog) return {success:false,error:'dialog 미열림',finalUrl:location.href};
  await new Promise(r=>setTimeout(r,1200));
  const field=(label)=>{ for(const e of dialog.querySelectorAll('*')){ if((e.textContent||'').trim()===label && e.children.length===0){ const s=e.nextElementSibling; if(!s)continue; const lk=s.querySelector('a')||(s.tagName==='A'?s:null); return (lk?.textContent||s.textContent||'').trim(); } } return ''; };
  let courierName=field('택배사'); let trackingNumber=field('송장번호');
  if(!trackingNumber){ for(const lk of dialog.querySelectorAll('a[href*="tracking"], a[href*="InvNo"]')){ const t=lk.textContent.trim(); if(/^\d{8,}$/.test(t)){trackingNumber=t;break;} } }
  return {success:!!trackingNumber,courierName,trackingNumber,finalUrl:location.href};
}
""",
}


def get_sw_ws():
    for t in requests.get(f"{CDP}/json", timeout=10).json():
        if t.get("type") == "service_worker" and EXT_ID in t.get("url", ""):
            return t.get("webSocketDebuggerUrl")
    return None


def main():
    mode, url = sys.argv[1], sys.argv[2]
    func = SCRAPE_FUNC[mode].strip()
    sw = get_sw_ws()
    expr = (
        "(async()=>{"
        f"const tab=await chrome.tabs.create({{url:{json.dumps(url)},active:false}});"
        "const tabId=tab.id;"
        "await new Promise(r=>setTimeout(r,16000));"
        f"const out=await chrome.scripting.executeScript({{target:{{tabId}},world:'MAIN',func:{func}}});"
        "try{await chrome.tabs.remove(tabId);}catch(e){}"
        "return out&&out[0]?out[0].result:{error:'no result'};"
        "})()"
    )
    ws = websocket.create_connection(sw, max_size=None, timeout=60)
    try:
        ws.send(json.dumps({"id": 1, "method": "Runtime.enable"}))
        time.sleep(0.3)
        ws.send(json.dumps({"id": 2, "method": "Runtime.evaluate", "params": {
            "expression": expr, "returnByValue": True, "awaitPromise": True, "timeout": 60000}}))
        end = time.time() + 60
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


if __name__ == "__main__":
    main()
