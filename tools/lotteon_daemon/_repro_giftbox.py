"""LOTTEON 선물주문 송장 추출 실패 재현 (헤드풀, 진단 전용).

데몬 자체 로그인 흐름 재사용 — 크레덴셜은 데몬 API로 내부 조회만 하고 절대 출력 안 함.
giftBoxDetail 페이지 도달 후: 버튼 목록 / '배송상세조회' 존재 / 택배사·송장 /
tracking_js raw 결과 / 스크린샷 덤프. 쿠키·비번·아이디·api_key 미출력.
"""

import asyncio
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import httpx
from playwright.async_api import async_playwright

# 데몬 모듈 재사용 (같은 디렉토리)
from daemon import (  # noqa: E402
    LOTTEON_HOME_URL,
    fetch_lotteon_credential,
    is_lotteon_logged_in,
    lotteon_auto_login,
)

ODNO = "2026060113922414"
URL = f"https://www.lotteon.com/p/order/claim/giftBoxDetail?odNo={ODNO}&type=snd"
BACKEND = "https://api.samba-wave.co.kr"
INSTALL = Path.home() / "AppData" / "Roaming" / "samba-autotune-daemon"
PROFILE = Path.home() / ".autotune_daemon" / "chromium_profile"
TMP_DIR = Path(__file__).parent / "_repro_out"

DIAG_JS = r"""
() => {
  const norm = s => (s||'').replace(/\s+/g,' ').trim();
  const buttons = [...document.querySelectorAll('button')].map(b=>norm(b.textContent)).filter(Boolean).slice(0,50);
  const links = [...document.querySelectorAll('a')].map(a=>norm(a.textContent)).filter(Boolean).slice(0,40);
  const hasDeliveryBtn = buttons.concat(links).some(t=>t.includes('배송상세조회'));
  const anyDelivery = buttons.concat(links).filter(t=>/배송|조회|상세|송장|운송장|택배/.test(t)).slice(0,20);
  const body = norm(document.body?.innerText||'').slice(0,1800);
  return {url: location.href, hasDeliveryBtn, anyDelivery, buttons, links, bodyHead: body};
}
"""

TRACKING_JS = r"""
(async () => {
  const href=location.href||'';
  if(href.indexOf('member.lotteon.com')!==-1 || href.indexOf('/member/login')!==-1)
    return {success:false, needsLogin:true, error:'needs_login redirect'};
  const txt=(document.body?.innerText||'').slice(0,8000);
  if(/(취소완료|취소처리완료|구매취소완료|주문이\s*취소|취소된\s*주문)/.test(txt)) return {success:false,cancelled:true,error:'order_cancelled'};
  let dialog=document.querySelector('dialog[open], [role="dialog"]');
  if(!dialog){
    const findBtn=()=>{ for(const b of document.querySelectorAll('button')){ if((b.textContent||'').trim().includes('배송상세조회'))return b; } for(const a of document.querySelectorAll('a')){ if((a.textContent||'').includes('배송상세조회'))return a; } return null; };
    let el=null; const tb=Date.now();
    while(Date.now()-tb<12000){ el=findBtn(); if(el)break; await new Promise(r=>setTimeout(r,400)); }
    if(!el) return {success:false, error:'no_tracking: 배송상세조회 버튼 없음 (미발송/선물주문)'};
    el.click();
    const td=Date.now();
    while(Date.now()-td<8000){ dialog=document.querySelector('dialog[open], [role="dialog"]'); if(dialog)break; await new Promise(r=>setTimeout(r,300)); }
    if(!dialog) return {success:false, error:'dialog 미열림'};
  }
  await new Promise(r=>setTimeout(r,1200));
  const field=(label)=>{ for(const e of dialog.querySelectorAll('*')){ if((e.textContent||'').trim()===label && e.children.length===0){ const s=e.nextElementSibling; if(!s)continue; const lk=s.querySelector('a')||(s.tagName==='A'?s:null); return (lk?.textContent||s.textContent||'').trim(); } } return ''; };
  let courierName=field('택배사'); let trackingNumber=field('송장번호');
  if(!trackingNumber) return {success:false, error:'no_tracking: 송장번호 미표시', courierName};
  return {success:true, courierName, trackingNumber};
})()
"""


def _read_secret(p: Path) -> str:
    try:
        v = p.read_text(encoding="utf-8").strip()
        return v[3:] if v.startswith("v2:") else v
    except Exception:
        return ""


async def main():
    device_id = _read_secret(INSTALL / "device_id.txt")
    api_key = _read_secret(PROFILE / "api_key.txt") or _read_secret(INSTALL / "api_key.txt")
    if not device_id or not api_key:
        print(f"[secret] device_id/api_key 없음 (did={bool(device_id)} key={bool(api_key)})")
        return

    TMP_DIR.mkdir(exist_ok=True)
    async with httpx.AsyncClient() as client:
        cred = await fetch_lotteon_credential(client, BACKEND, device_id, api_key)
    if not cred:
        print("[cred] LOTTEON 크레덴셜 조회 실패 (위 로그 참조)")
        return
    print(f"[cred] 조회 OK (계정 username 길이={len(cred['username'])}, 값 미출력)")

    async with async_playwright() as pw:
        browser = None
        for ch in ("chrome", "msedge"):
            try:
                browser = await pw.chromium.launch(
                    channel=ch, headless=False,
                    args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
                )
                print(f"[browser] 시스템 {ch}")
                break
            except Exception as e:
                print(f"[browser] {ch} 실패: {str(e)[:60]}")
        if not browser:
            browser = await pw.chromium.launch(headless=False, args=["--no-sandbox"])
            print("[browser] 번들")

        ctx = await browser.new_context()
        page = await ctx.new_page()

        ok = await lotteon_auto_login(page, cred)
        print(f"[login] lotteon_auto_login = {ok}")
        confirmed = await is_lotteon_logged_in(page)
        print(f"[login] is_lotteon_logged_in = {confirmed}")

        print(f"[goto] {URL}")
        try:
            await page.goto(URL, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            print(f"[goto] 예외: {str(e)[:120]}")
        await page.wait_for_timeout(4500)

        diag = await page.evaluate(DIAG_JS)
        print("\n===== 페이지 진단 =====")
        print(f"final_url        = {diag['url']}")
        print(f"배송상세조회버튼 = {diag['hasDeliveryBtn']}")
        print(f"배송관련 텍스트  = {diag['anyDelivery']}")
        print(f"buttons({len(diag['buttons'])}) = {diag['buttons']}")
        print(f"links({len(diag['links'])})   = {diag['links']}")
        print(f"\nbodyHead:\n{diag['bodyHead']}")

        shot = TMP_DIR / "giftbox.png"
        await page.screenshot(path=str(shot), full_page=True)
        print(f"\n[screenshot] {shot}")

        raw = await page.evaluate(TRACKING_JS)
        print(f"\n===== tracking_js RAW =====\n{raw}")

        await ctx.close()
        await browser.close()


asyncio.run(main())
