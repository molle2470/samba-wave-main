# LOTTEON CSS 차단 안전성 검증 — 현재(image/media/font 차단) vs 제안(+stylesheet 차단)
# 가격(나의 혜택가) 파싱·트래픽 비교. LOTTEON 은 로그인 필수 → storage_state 쿠키 사용
# (사용자가 이 PC 에서 LOTTEON 데몬 실행 후 쿠키가 저장돼 있어야 함).
#
# 상품 URL: 인자로 직접 전달하거나(권장), 미전달 시 공개 랭킹페이지에서 자동 발견 시도.
#   사용:  python _css_block_test_lotteon.py "<url1>" "<url2>" "<url3>"
import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import daemon  # noqa: E402  # SITE_HANDLERS["LOTTEON"] 등록 트리거
from site_handlers import SITE_HANDLERS  # noqa: E402
from playwright.async_api import async_playwright  # noqa: E402

H = SITE_HANDLERS["LOTTEON"]
PROFILE = Path.home() / ".autotune_daemon" / "chromium_profile" / "storage_state.json"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
# 공개 베스트/랭킹 페이지 — /p/product/{id} 링크 추출용
DISCOVER_URL = "https://www.lotteon.com/p/display/main/seller?mall_no=1"


async def discover_urls(pw, n=3):
    browser = await pw.chromium.launch(headless=True)
    ctx = await browser.new_context(user_agent=UA, viewport={"width": 1280, "height": 900})
    page = await ctx.new_page()
    urls = []
    try:
        await page.goto(DISCOVER_URL, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(4000)
        html = await page.content()
        found = re.findall(r"/p/product/(\d{6,})", html)
        seen = set()
        for pid in found:
            if pid in seen:
                continue
            seen.add(pid)
            urls.append(f"https://www.lotteon.com/p/product/{pid}")
            if len(urls) >= n:
                break
    finally:
        await browser.close()
    return urls


async def run_mode(pw, urls, block_css):
    browser = await pw.chromium.launch(headless=True)
    ctx_kwargs = {"user_agent": UA, "viewport": {"width": 1280, "height": 900}}
    if PROFILE.exists():
        ctx_kwargs["storage_state"] = str(PROFILE)
    ctx = await browser.new_context(**ctx_kwargs)
    blocked = (
        ("image", "media", "font", "stylesheet")
        if block_css
        else ("image", "media", "font")
    )

    async def route(r):
        if r.request.resource_type in blocked:
            await r.abort()
        else:
            await r.continue_()

    await ctx.route("**/*", route)
    bt = {}

    def on_finished(req):
        async def _go():
            try:
                s = await req.sizes()
                rt = req.resource_type
                bt[rt] = bt.get(rt, 0) + max(0, s.get("responseBodySize", 0))
            except Exception:
                pass

        asyncio.create_task(_go())

    ctx.on("requestfinished", on_finished)

    results = []
    for url in urls:
        page = await ctx.new_page()
        try:
            await page.goto(url, wait_until="commit", timeout=30000)
        except Exception as e:
            results.append((url, {"success": False, "error": f"goto:{e}"}, 0))
            await page.close()
            continue
        deadline = H.pre_extract_marker_timeout_ms
        step = 500
        el = 0
        while el < deadline:
            try:
                hit = await page.evaluate(H.pre_extract_marker_js)
            except Exception:
                hit = False
            if hit:
                break
            await page.wait_for_timeout(step)
            el += step
        await page.wait_for_timeout(H.pre_extract_wait_ms)
        try:
            data = await page.evaluate(H.extract_js)
        except Exception as e:
            data = {"success": False, "error": f"evaluate:{e}"}
        if not isinstance(data, dict):
            data = {"success": False, "error": "non-dict"}
        results.append(
            (
                url.split("/p/product/")[-1],
                {
                    k: data.get(k)
                    for k in (
                        "success",
                        "best_benefit_price",
                        "sale_price",
                        "original_price",
                        "login_required",
                    )
                },
                len(data.get("options") or []),
            )
        )
        await page.wait_for_timeout(600)
        await page.close()
    await asyncio.sleep(1.0)
    await browser.close()
    return results, bt


async def main():
    arg_urls = [a for a in sys.argv[1:] if a.startswith("http")]
    async with async_playwright() as pw:
        if arg_urls:
            urls = arg_urls
            print(f"인자 URL {len(urls)}개 사용")
        else:
            print("LOTTEON 공개 페이지에서 상품 URL 발견 중...")
            urls = await discover_urls(pw, 3)
        if not urls:
            print("URL 없음 — 인자로 직접 전달: python _css_block_test_lotteon.py <url1> <url2> <url3>")
            return
        print(f"대상 URL: {urls}")
        if not PROFILE.exists():
            print(f"⚠️ storage_state 없음({PROFILE}) — 로그인 안 됨 → 혜택가 미표시 가능. LOTTEON 데몬 먼저 실행 필요.\n")
        else:
            print(f"쿠키 사용: {PROFILE} ({round(PROFILE.stat().st_size / 1024)}KB)\n")

        print("=== MODE A: 현재 데몬(image/media/font 차단) ===")
        rA, bA = await run_mode(pw, urls, False)
        for r in rA:
            print(" ", r)
        tA = sum(bA.values())
        print("  bytes:", {k: f"{v // 1024}KB" for k, v in sorted(bA.items(), key=lambda x: -x[1])})
        print(f"  TOTAL: {tA // 1024}KB\n")

        print("=== MODE B: 제안(+stylesheet 차단) ===")
        rB, bB = await run_mode(pw, urls, True)
        for r in rB:
            print(" ", r)
        tB = sum(bB.values())
        print("  bytes:", {k: f"{v // 1024}KB" for k, v in sorted(bB.items(), key=lambda x: -x[1])})
        print(f"  TOTAL: {tB // 1024}KB\n")
        if tA:
            print(f"==> 트래픽 절감: {tA // 1024}KB -> {tB // 1024}KB ({100 * (tA - tB) // tA}% 감소)")


asyncio.run(main())
