"""웨일(9223) CDP 드라이버 — 송장 스크랩 검증.

사용: python cdp_tracking_probe.py <mode> <url>
mode: ssg | abc | lotteon | musinsa
실제 송장조회 페이지를 새 탭으로 열고, 해당 사이트 스크랩 JS 를 실행해
{success, courierName, trackingNumber, error, finalUrl} 반환.
확장앱 content-tracking-*.js 로직을 self-contained async 함수로 이식.
"""

import json
import sys
import time

import requests
import websocket

CDP = "http://localhost:9223"

# ── 사이트별 스크랩 JS (확장앱 content-tracking-*.js 이식, 단일 evaluate용) ──
SCRAPE_JS = {
    # SSG — 로그인 불필요. .tx_state em 읽기
    "ssg": r"""
(async () => {
  const href = location.href || '';
  if (href.indexOf('member.ssg.com')!==-1 || href.indexOf('/member/login')!==-1 || href.indexOf('orderInfoDetail.ssg')===-1)
    return {success:false, error:'needs_login/redirect', finalUrl:href};
  const t0=Date.now(); let c=null;
  while(Date.now()-t0<12000){ c=document.querySelector('.tx_state em'); if(c) break; await new Promise(r=>setTimeout(r,300)); }
  if(!c) return {success:false, error:'no .tx_state em (미발송 or 미로드)', finalUrl:location.href};
  const courierName=(c.querySelector('span')?.textContent||'').trim();
  let trackingNumber='';
  for(const n of c.childNodes){ if(n.nodeType===3){ const m=n.textContent.match(/\d{8,}/); if(m){trackingNumber=m[0];break;} } }
  return {success:!!trackingNumber, courierName, trackingNumber, finalUrl:location.href};
})()
""",
    # ABCmart/GrandStage — 로그인 필요. div.status-info 읽기
    "abc": r"""
(async () => {
  const href=location.href||'';
  if(href.indexOf('/member/login')!==-1 || href.indexOf('order-detail')===-1)
    return {success:false, error:'needs_login/redirect', finalUrl:href};
  const t0=Date.now(); let c=null;
  while(Date.now()-t0<10000){ c=document.querySelector('div.status-info .info-desc'); if(c&&(c.textContent||'').trim()) break; await new Promise(r=>setTimeout(r,300)); }
  if(!c) return {success:false, error:'no status-info (미발송 or 미로드)', finalUrl:location.href};
  const courierName=c.textContent.trim();
  const te=document.querySelector('div.status-info .info-link');
  const raw=(te?.textContent||'').trim(); const m=raw.match(/\d{8,}/);
  const trackingNumber=m?m[0]:raw;
  return {success:!!trackingNumber, courierName, trackingNumber, finalUrl:location.href};
})()
""",
    # LOTTEON — 로그인 필요. "배송상세조회" 클릭 → dialog 읽기
    "lotteon": r"""
(async () => {
  const href=location.href||'';
  if(href.indexOf('member.lotteon.com')!==-1 || href.indexOf('/member/login')!==-1)
    return {success:false, error:'needs_login redirect', finalUrl:href};
  const txt=(document.body?.innerText||'').slice(0,8000);
  if(/(취소완료|취소처리완료|구매취소완료|주문이\s*취소|취소된\s*주문)/.test(txt)) return {success:false,error:'order_cancelled',finalUrl:href};
  let dialog=document.querySelector('dialog[open], [role="dialog"]');
  if(!dialog){
    // SPA 본문 늦게 로드 — 배송상세조회 버튼을 최대 15초 폴링
    const findBtn=()=>{ for(const b of document.querySelectorAll('button')){ const t=b.textContent.trim(); if(t==='배송상세조회'||t==='배송 상세 조회'||t==='배송상세 조회'||t.includes('배송상세조회')) return b; } for(const a of document.querySelectorAll('a')){ if(a.textContent.trim().includes('배송상세조회')) return a; } return null; };
    let el=null; const tb=Date.now();
    while(Date.now()-tb<15000){ el=findBtn(); if(el)break; await new Promise(r=>setTimeout(r,400)); }
    if(!el) return {success:false, error:'배송상세조회 버튼 없음 (미발송?)', bodyHead:(document.body?.innerText||'').slice(0,400), finalUrl:location.href};
    el.click();
    const t0=Date.now();
    while(Date.now()-t0<6000){ dialog=document.querySelector('dialog[open], [role="dialog"]'); if(dialog)break; await new Promise(r=>setTimeout(r,300)); }
    if(!dialog) return {success:false, error:'dialog 미열림', finalUrl:href};
  }
  await new Promise(r=>setTimeout(r,1000));
  const field=(label)=>{ for(const el of dialog.querySelectorAll('*')){ if((el.textContent||'').trim()===label && el.children.length===0){ const sib=el.nextElementSibling; if(!sib)continue; const link=sib.querySelector('a')||(sib.tagName==='A'?sib:null); return (link?.textContent||sib.textContent||'').trim(); } } return ''; };
  let courierName=field('택배사'); let trackingNumber=field('송장번호');
  if(!trackingNumber){ for(const link of dialog.querySelectorAll('a[href*="tracking"], a[href*="InvNo"]')){ const t=link.textContent.trim(); if(/^\d{8,}$/.test(t)){trackingNumber=t;break;} } }
  return {success:!!trackingNumber, courierName, trackingNumber, finalUrl:location.href};
})()
""",
    # MUSINSA order-detail — "배송조회" 버튼 클릭 → SPA nav 감지. 클릭만 하고 nav 여부 보고
    "musinsa": r"""
(async () => {
  const href=location.href||'';
  if(href.indexOf('/auth/login')!==-1 || href.indexOf('member.one.musinsa.com')!==-1)
    return {success:false, error:'needs_login redirect', finalUrl:href};
  const bodyHead=(document.body?.innerText||'').slice(0,300);
  const isWrong=()=>{ const t=(document.body?.innerText||'').slice(0,4000); return /주문\s*정보를?\s*찾을\s*수\s*없|잘못된\s*접근/.test(t); };
  const t0=Date.now(); let btn=null;
  while(Date.now()-t0<15000){
    if(isWrong()) return {success:false, error:'wrong_account (현 로그인 계정에 주문 없음)', finalUrl:location.href, bodyHead};
    const bs=Array.from(document.querySelectorAll('button'));
    btn=bs.find(b=>{ const t=(b.textContent||'').replace(/\s+/g,'').trim(); return t==='배송조회'||t.includes('배송조회'); });
    if(btn && !btn.disabled) break;
    await new Promise(r=>setTimeout(r,300));
  }
  if(!btn) return {success:false, error:'배송조회 버튼 없음 (배송대기/미발송?)', finalUrl:location.href, bodyHead};
  btn.click();
  const ns=Date.now();
  while(Date.now()-ns<20000){
    await new Promise(r=>setTimeout(r,400));
    if(/\/order-service\/my\/delivery\/trace/.test(location.pathname)){
      const t1=Date.now(); let ce=null;
      while(Date.now()-t1<20000){ ce=document.querySelector('p.company-name'); if(ce&&(ce.textContent||'').trim())break; await new Promise(r=>setTimeout(r,300)); }
      if(!ce) return {success:false, error:'trace 진입했으나 company-name 미로드', finalUrl:location.href};
      const courierName=ce.textContent.trim();
      const te=document.querySelector('button.tracking-number');
      const trackingNumber=(te?.textContent||'').trim();
      return {success:!!trackingNumber, courierName, trackingNumber, finalUrl:location.href};
    }
  }
  return {success:false, error:'배송조회 클릭했으나 trace 미진입(SPA nav 실패 or 미발송)', finalUrl:location.href};
})()
""",
}


def cdp_send(ws, mid, method, params=None):
    ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))


def cdp_wait(ws, mid, timeout=40):
    end = time.time() + timeout
    while time.time() < end:
        ws.settimeout(max(1, end - time.time()))
        try:
            msg = json.loads(ws.recv())
        except Exception:
            continue
        if msg.get("id") == mid:
            return msg
    return None


def main():
    mode = sys.argv[1]
    url = sys.argv[2]
    js = SCRAPE_JS[mode]

    # 새 탭 생성 (Chrome 146 — PUT /json/new)
    r = requests.put(f"{CDP}/json/new?{url}", timeout=10)
    tab = r.json()
    target_id = tab["id"]
    ws_url = tab["webSocketDebuggerUrl"]
    print(f"[탭생성] {target_id} → {url}")

    ws = websocket.create_connection(ws_url, max_size=None)
    try:
        cdp_send(ws, 1, "Page.enable")
        cdp_wait(ws, 1)
        cdp_send(ws, 2, "Runtime.enable")
        cdp_wait(ws, 2)
        # 배경 탭 렌더 스로틀 회피 — 탭 활성화
        cdp_send(ws, 3, "Page.bringToFront")
        cdp_wait(ws, 3)
        # 페이지 로드 대기
        time.sleep(6)
        cdp_send(
            ws,
            10,
            "Runtime.evaluate",
            {"expression": js, "awaitPromise": True, "returnByValue": True, "timeout": 60000},
        )
        res = cdp_wait(ws, 10, timeout=70)
        if res and "result" in res:
            val = res["result"].get("result", {}).get("value")
            print(f"[결과] {json.dumps(val, ensure_ascii=False)}")
        else:
            print(f"[에러] {json.dumps(res, ensure_ascii=False)[:500]}")
    finally:
        ws.close()
        try:
            requests.get(f"{CDP}/json/close/{target_id}", timeout=5)
        except Exception:
            pass


if __name__ == "__main__":
    main()
