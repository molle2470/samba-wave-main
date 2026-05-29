# CSS 차단 안전성 검증 — 데몬과 동일 경로(site_handlers + storage_state 쿠키)로
# 현재(image/media/font 차단) vs 제안(+stylesheet 차단) 가격 파싱·트래픽 비교.
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from site_handlers import SITE_HANDLERS  # noqa: E402
from playwright.async_api import async_playwright  # noqa: E402

PRDTS = ["1020117462", "1020109247", "1020121594"]
PROFILE = Path.home() / ".autotune_daemon" / "chromium_profile" / "storage_state.json"
H = SITE_HANDLERS["ABCmart"]
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


async def run_mode(pw, block_css):
    browser = await pw.chromium.launch(headless=True)
    ctx = await browser.new_context(
        storage_state=str(PROFILE),
        viewport={"width": 1280, "height": 900},
        user_agent=UA,
    )
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
    for prdt in PRDTS:
        page = await ctx.new_page()
        url = f"https://abcmart.a-rt.com/product/new?prdtNo={prdt}"
        await page.goto(url, wait_until="commit", timeout=30000)
        await page.evaluate(f"window.__PRD_ID__={json.dumps(prdt)}")
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
            data = {"success": False, "error": f"evaluate예외:{e}"}
        if not isinstance(data, dict):
            data = {"success": False, "error": "non-dict"}
        results.append(
            (
                prdt,
                {
                    k: data.get(k)
                    for k in ("success", "sale_price", "best_benefit_price", "name")
                },
                len(data.get("options") or []),
            )
        )
        await page.wait_for_timeout(800)
        await page.close()
    await asyncio.sleep(1.0)  # 잔여 sizes() task 수집
    await browser.close()
    return results, bt


async def main():
    async with async_playwright() as pw:
        print("=== MODE A: 현재 데몬(image/media/font 차단) ===")
        rA, bA = await run_mode(pw, False)
        for r in rA:
            print(" ", r)
        tA = sum(bA.values())
        print(
            "  bytes:",
            {k: f"{v // 1024}KB" for k, v in sorted(bA.items(), key=lambda x: -x[1])},
        )
        print(f"  TOTAL: {tA // 1024}KB")
        print()
        print("=== MODE B: 제안(image/media/font/stylesheet 차단) ===")
        rB, bB = await run_mode(pw, True)
        for r in rB:
            print(" ", r)
        tB = sum(bB.values())
        print(
            "  bytes:",
            {k: f"{v // 1024}KB" for k, v in sorted(bB.items(), key=lambda x: -x[1])},
        )
        print(f"  TOTAL: {tB // 1024}KB")
        print()
        if tA:
            print(f"==> 트래픽 절감: {tA // 1024}KB -> {tB // 1024}KB ({100 * (tA - tB) // tA}% 감소)")


asyncio.run(main())
