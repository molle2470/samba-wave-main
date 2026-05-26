"""데몬 송장 흐름 로컬 검증 — 폴링 루프 없이 새 코드 경로만 직접 실행.

ensure_logged_in_as_account + extract_tracking 을 LOTTEON 알려진 주문에 돌려
정답 송장과 대조. 실제 잡 큐는 건드리지 않음.

실행: .venv/Scripts/python.exe _test_tracking_local.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import httpx  # noqa: E402
from playwright.async_api import async_playwright  # noqa: E402

import daemon as D  # noqa: E402

BACKEND = "https://api.samba-wave.co.kr"
DEVICE_ID = D._default_device_id()
API_KEY = Path.home().joinpath(".autotune_daemon", "chromium_profile", "api_key.txt").read_text().strip()

# (site, account_id, order_no, tracking_url, 정답 courier/track)
CASES = [
    (
        "LOTTEON",
        "sa_01KP88WDDXWHNYA2EPTF1DQCRA",
        "2026051719354254",
        "https://www.lotteon.com/p/order/claim/orderDetail?odNo=2026051719354254",
        ("롯데택배", "318447794733"),
    ),
]


async def main():
    print(f"device_id={DEVICE_ID} api_key={'있음' if API_KEY else '없음'}({len(API_KEY)}자)")
    storage = Path.home() / ".autotune_daemon" / "chromium_profile" / "storage_state.json"
    async with async_playwright() as pw:
        browser = await D._launch_browser(pw, headless=True)
        # 데몬과 동일하게 영속 세션(storage_state) 로드 → 살아있는 로그인 재사용
        ctx = await browser.new_context(
            storage_state=str(storage) if storage.exists() else None
        )
        page = await ctx.new_page()
        async with httpx.AsyncClient() as client:
            for site, acc, ordno, url, (exp_c, exp_t) in CASES:
                handler = D.SITE_HANDLERS[site]
                print(f"\n=== {site} ord={ordno} acc={acc} ===")
                # 세션 살아있나 먼저 확인 (storage_state 재사용)
                alive = await D.is_site_logged_in(page, handler)
                print(f"  세션 살아있음: {alive}")
                if not alive:
                    login_ok = await D.ensure_logged_in_as_account(
                        page, client, BACKEND, DEVICE_ID, API_KEY, handler, acc
                    )
                    print(f"  로그인: {login_ok}")
                    if not login_ok:
                        print("  → 로그인 실패, 스크랩 스킵")
                        continue
                # 스크랩 검증 (extract_tracking — 새 코드 경로)
                data = await D.extract_tracking(page, url, handler)
                print(f"  스크랩: {data}")
                c, t = data.get("courierName"), data.get("trackingNumber")
                match = (c == exp_c and t == exp_t)
                print(f"  정답대조: {'✅ 일치' if match else f'❌ 불일치 (정답 {exp_c}/{exp_t})'}")
        await browser.close()


asyncio.run(main())
