"""SSG/MUSINSA 데몬 송장 스크랩 검증 — 웨일 쿠키 브리지.

데몬 브라우저엔 SSG/MUSINSA 세션이 없으므로, 로그인된 웨일(9223)의 쿠키를
storage_state 로 가져와 데몬 컨텍스트에 주입 → extract_tracking 검증.
(로그인 자체는 captcha 위험이라 별도 — 여기선 스크랩/2단계 nav 로직만 검증)
"""

import asyncio
import json
import sys
import time
from pathlib import Path

import requests
import websocket

sys.path.insert(0, str(Path(__file__).parent))
from playwright.async_api import async_playwright  # noqa: E402

import daemon as D  # noqa: E402

CDP = "http://localhost:9223"

# (site, order_no, tracking_url, 정답 courier/track)
CASES = [
    ("SSG", "20260520179F76",
     "https://pay.ssg.com/myssg/orderInfoDetail.ssg?orordNo=20260520179F76",
     ("로젠택배", "91929850681")),
    ("MUSINSA", "202605221407290006",
     "https://www.musinsa.com/order/order-detail/202605221407290006",
     ("CJ대한통운", "305335469635")),
]

_SS_MAP = {"Strict": "Strict", "Lax": "Lax", "None": "None", "": "Lax", None: "Lax"}


def whale_cookies():
    """웨일 브라우저 전체 쿠키 → Playwright storage_state cookies 포맷."""
    bws = requests.get(f"{CDP}/json/version", timeout=10).json()["webSocketDebuggerUrl"]
    ws = websocket.create_connection(bws, max_size=None, timeout=20)
    try:
        ws.send(json.dumps({"id": 1, "method": "Storage.getCookies"}))
        end = time.time() + 15
        while time.time() < end:
            ws.settimeout(max(1, end - time.time()))
            try:
                m = json.loads(ws.recv())
            except Exception:
                continue
            if m.get("id") == 1:
                raw = m["result"]["cookies"]
                break
        else:
            return []
    finally:
        ws.close()
    out = []
    for c in raw:
        dom = c.get("domain", "")
        if not any(k in dom for k in ("ssg.com", "musinsa.com", "lotteon", "a-rt.com")):
            continue
        ck = {
            "name": c["name"], "value": c["value"], "domain": dom,
            "path": c.get("path", "/"),
            "expires": c.get("expires", -1) if c.get("expires", -1) > 0 else -1,
            "httpOnly": c.get("httpOnly", False), "secure": c.get("secure", False),
            "sameSite": _SS_MAP.get(c.get("sameSite"), "Lax"),
        }
        out.append(ck)
    return out


async def main():
    cookies = whale_cookies()
    print(f"웨일 쿠키 브리지: {len(cookies)}개 (ssg/musinsa/lotteon/a-rt)")
    storage = {"cookies": cookies, "origins": []}
    async with async_playwright() as pw:
        browser = await D._launch_browser(pw, headless=True)
        ctx = await browser.new_context(storage_state=storage)
        page = await ctx.new_page()
        for site, ordno, url, (exp_c, exp_t) in CASES:
            handler = D.SITE_HANDLERS[site]
            print(f"\n=== {site} ord={ordno} ===")
            data = await D.extract_tracking(page, url, handler)
            print(f"  스크랩: {data}")
            c, t = data.get("courierName"), data.get("trackingNumber")
            match = (c == exp_c and t == exp_t)
            print(f"  정답대조: {'✅ 일치' if match else f'❌ (정답 {exp_c}/{exp_t})'}")
        await browser.close()


asyncio.run(main())
