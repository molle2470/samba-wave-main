# SSG CSS 차단 안전성 검증 — 공개 검색페이지에서 itemId 자동 발견 후
# 현재(image/media/font 차단) vs 제안(+stylesheet 차단) 가격 파싱·트래픽 비교.
# 프로덕션 DB 불필요 — SSG 공개 페이지만 사용.
import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from site_handlers import SITE_HANDLERS  # noqa: E402
from playwright.async_api import async_playwright  # noqa: E402

H = SITE_HANDLERS["SSG"]
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
SEARCH_URL = "https://www.ssg.com/search.ssg?target=all&query=%EB%82%98%EC%9D%B4%ED%82%A4"


async def discover_item_ids(pw, n=3):
    """SSG 공개 검색페이지에서 itemView itemId 추출."""
    browser = await pw.chromium.launch(headless=True)
    ctx = await browser.new_context(user_agent=UA, viewport={"width": 1280, "height": 900})
    page = await ctx.new_page()
    ids = []
    try:
        await page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)
        html = await page.content()
        # itemView.ssg?itemId=NNNN 또는 data-itemid 추출
        found = re.findall(r"itemId=(\d{6,})", html)
        found += re.findall(r'data-item[iI]d="(\d{6,})"', html)
        seen = set()
        for i in found:
            if i not in seen:
                seen.add(i)
                ids.append(i)
            if len(ids) >= n:
                break
    finally:
        await browser.close()
    return ids


async def run_mode(pw, item_ids, block_css):
    browser = await pw.chromium.launch(headless=True)
    ctx = await browser.new_context(user_agent=UA, viewport={"width": 1280, "height": 900})
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
    for iid in item_ids:
        page = await ctx.new_page()
        url = f"https://www.ssg.com/item/itemView.ssg?itemId={iid}"
        try:
            await page.goto(url, wait_until="commit", timeout=30000)
        except Exception as e:
            results.append((iid, {"success": False, "error": f"goto:{e}"}, 0))
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
                iid,
                {
                    k: data.get(k)
                    for k in (
                        "success",
                        "salePrice",
                        "sale_price",
                        "domCardPrice",
                        "domSalePrice",
                        "cost",
                        "originalPrice",
                    )
                },
                len(data.get("options") or data.get("uitemOptions") or []),
            )
        )
        await page.wait_for_timeout(600)
        await page.close()
    await asyncio.sleep(1.0)
    await browser.close()
    return results, bt


async def main():
    async with async_playwright() as pw:
        print("SSG 검색페이지에서 itemId 발견 중...")
        ids = await discover_item_ids(pw, 3)
        if not ids:
            print("itemId 발견 실패 — SSG 검색 DOM 변경 또는 차단. 수동 URL 필요.")
            return
        print(f"발견된 itemId: {ids}\n")

        print("=== MODE A: 현재 데몬(image/media/font 차단) ===")
        rA, bA = await run_mode(pw, ids, False)
        for r in rA:
            print(" ", r)
        tA = sum(bA.values())
        print("  bytes:", {k: f"{v // 1024}KB" for k, v in sorted(bA.items(), key=lambda x: -x[1])})
        print(f"  TOTAL: {tA // 1024}KB\n")

        print("=== MODE B: 제안(+stylesheet 차단) ===")
        rB, bB = await run_mode(pw, ids, True)
        for r in rB:
            print(" ", r)
        tB = sum(bB.values())
        print("  bytes:", {k: f"{v // 1024}KB" for k, v in sorted(bB.items(), key=lambda x: -x[1])})
        print(f"  TOTAL: {tB // 1024}KB\n")
        if tA:
            print(f"==> 트래픽 절감: {tA // 1024}KB -> {tB // 1024}KB ({100 * (tA - tB) // tA}% 감소)")


asyncio.run(main())
