# -*- coding: utf-8 -*-
"""10개 패션 브랜드 10개 상품 수집→마켓등록 변환 정확도 검증 (dry-run).

- 실제 마켓 POST 없음. 수집(get_goods_detail) + 마켓별 transform 페이로드만 검사.
- 프로덕션 DB 미오염 (저장 안 함). 무신사 서버사이드 fetch(UA만).

실행: backend/.venv/Scripts/python.exe scripts/verify_collect_register.py
"""

import asyncio
import sys
import traceback

# 10개 패션 브랜드 (무신사) — 각 1상품
BRANDS = [
    "커버낫",
    "디스이즈네버댓",
    "리바이스",
    "노스페이스",
    "데상트",
    "뉴발란스",
    "아디다스",
    "반스",
    "휠라",
    "게스",
]

# dry-run transform 대상 핵심 마켓
TARGET_MARKETS = [
    "smartstore",
    "11st",
    "coupang",
    "gmarket",
    "auction",
    "ssg",
    "lotteon",
    "lottehome",
    "gsshop",
    "playauto",
]
TEST_CATEGORY = "50000000"  # 임의 숫자 카테고리(변환 스모크용)


def _assert_collect(detail: dict) -> list[str]:
    """수집 정확도 검증 — 문제 목록 반환(빈 리스트 = OK)."""
    issues = []
    if not (detail.get("name") or "").strip():
        issues.append("name 비어있음")
    if not (detail.get("brand") or "").strip():
        issues.append("brand 비어있음")
    sp = detail.get("salePrice") or 0
    if sp <= 0:
        issues.append(f"salePrice 비정상({sp})")
    op = detail.get("originalPrice") or 0
    if op < sp:
        issues.append(f"originalPrice({op}) < salePrice({sp})")
    opts = detail.get("options") or []
    if not opts:
        issues.append("options 0개")
    else:
        for o in opts:
            if not isinstance(o, dict):
                issues.append("옵션 형식 비정상")
                break
            if "stock" not in o and "isSoldOut" not in o:
                issues.append("옵션에 stock/isSoldOut 없음")
                break
    imgs = detail.get("images") or []
    if not imgs:
        issues.append("images 0개")
    if not (detail.get("category") or "").strip():
        issues.append("category 비어있음")
    return issues


async def main():
    sys.path.insert(0, ".")
    from backend.domain.samba.proxy.musinsa import MusinsaClient
    from backend.api.v1.routers.samba.collector_common import _build_product_data
    from backend.domain.samba.collector.model import SambaCollectedProduct
    from backend.domain.samba.plugins import MARKET_PLUGINS
    from backend.domain.samba.shipment.service import SambaShipmentService

    # 웨일에서 추출한 무신사 로그인 쿠키 (get_musinsa_cookie.py 산출)
    cookie = ""
    try:
        with open(r"C:\temp\musinsa_cookie.txt", encoding="utf-8") as f:
            cookie = f.read().strip()
    except FileNotFoundError:
        pass
    if not cookie:
        print("❌ 무신사 쿠키 없음 — C:/temp/get_musinsa_cookie.py 먼저 실행")
        return 1
    client = MusinsaClient(cookie=cookie)

    model_fields = set(SambaCollectedProduct.model_fields.keys())
    report = []
    overall_ok = True

    for i, brand in enumerate(BRANDS, 1):
        line = {"brand": brand, "goods_no": None, "collect": [], "transform": {}}
        try:
            # 1) 검색 → 첫 재고있는 상품
            search = await client.search_products(brand, size=10)
            prods = (search.get("data") if isinstance(search, dict) else search) or []
            goods_no = None
            for p in prods:
                if not p.get("isSoldOut"):
                    goods_no = p.get("siteProductId") or p.get("goodsNo")
                    break
            if not goods_no and prods:
                goods_no = prods[0].get("siteProductId")
            if not goods_no:
                line["collect"] = ["검색결과 0개"]
                overall_ok = False
                report.append(line)
                print(f"[{i}/10] {brand}: 검색결과 0개")
                continue
            line["goods_no"] = goods_no

            # 2) 상세 수집
            detail = await client.get_goods_detail(str(goods_no))

            # 3) 수집 정확도 검증
            issues = _assert_collect(detail)
            line["collect"] = issues
            if issues:
                overall_ok = False

            # 4) 저장 dict 빌드(미저장) → DB row 형태 dict
            cat = detail.get("category", "")
            cat_parts = [c for c in cat.split(" > ") if c] if cat else []
            pd = _build_product_data(
                detail,
                str(goods_no),
                "verify-test",
                "MUSINSA",
                detail.get("bestBenefitPrice") or detail.get("salePrice") or 0,
                detail.get("salePrice") or 0,
                detail.get("originalPrice") or 0,
                cat,
                cat_parts,
                detail.get("detailHtml", ""),
            )
            row = SambaCollectedProduct(
                **{k: v for k, v in pd.items() if k in model_fields}
            )
            product_dict = row.model_dump(exclude={"last_sent_data", "extra_data"})
            product_dict["actual_size"] = (pd.get("extra_data") or {}).get("actualSize")
            # 실측표 HTML(있으면) 포함된 detail_html 합성
            size_html = SambaShipmentService._build_size_chart_html(
                product_dict["actual_size"]
            )
            product_dict["detail_html"] = (
                detail.get("detailHtml", "") or ""
            ) + size_html

            # 실측표 수집 여부(정보) — 무신사는 상품마다 실측 유무 다름(없으면 None 정상)
            line["size_chart_len"] = len(size_html)
            line["has_actual_size"] = product_dict["actual_size"] is not None

            # 5) 마켓별 transform dry-run
            for mt in TARGET_MARKETS:
                plugin = MARKET_PLUGINS.get(mt)
                if not plugin:
                    line["transform"][mt] = "플러그인 없음"
                    continue
                try:
                    payload = plugin.transform(dict(product_dict), TEST_CATEGORY)
                    # dict(JSON 마켓) 또는 str(11번가 등 XML 마켓) 모두 허용
                    if isinstance(payload, str):
                        ok = len(payload.strip()) > 100
                    elif isinstance(payload, dict):
                        ok = bool(payload)
                    else:
                        ok = False
                    if not ok:
                        line["transform"][mt] = f"빈 페이로드({type(payload).__name__})"
                        overall_ok = False
                    else:
                        line["transform"][mt] = "OK"
                except Exception as e:
                    line["transform"][mt] = f"EXC: {e}"
                    overall_ok = False

            # 5-1) 가격·옵션 정확도 단언 (대표 마켓: smartstore JSON, 11st XML)
            price_issues = []
            sp = int(product_dict.get("sale_price") or 0)
            op = int(product_dict.get("original_price") or 0)
            n_opt = len(product_dict.get("options") or [])
            try:
                import math as _math

                ss = MARKET_PLUGINS["smartstore"].transform(
                    dict(product_dict), TEST_CATEGORY
                )
                prod = ss.get("originProduct") or ss
                # 스마트스토어 규칙: 300원 올림 후 네이버 수수료 그로스업(*4//3)
                # (smartstore.py:1872-1874)
                _dp = _math.ceil(sp / 300) * 300 if sp > 0 else 0
                expect_ss = _dp * 4 // 3
                if int(prod.get("salePrice") or 0) != expect_ss:
                    price_issues.append(f"ss가격 {prod.get('salePrice')}≠{expect_ss}")
                oc = ((prod.get("detailAttribute") or {}).get("optionInfo") or {}).get(
                    "optionCombinations"
                ) or []
                if n_opt and len(oc) != n_opt:
                    price_issues.append(f"ss옵션 {len(oc)}≠{n_opt}")
            except Exception as e:
                price_issues.append(f"ss단언EXC:{e}")
            try:
                xml = MARKET_PLUGINS["11st"].transform(
                    dict(product_dict), TEST_CATEGORY
                )
                if isinstance(xml, str):
                    import re as _re

                    m_sel = _re.search(r"<selPrc>(\d+)</selPrc>", xml)
                    m_mak = _re.search(r"<maktPrc>(\d+)</maktPrc>", xml)
                    # 11번가 규칙: 판매가 100원 내림, 정상가도 100원 내림(>판매가일때)
                    # (elevenst.py:2010,2013)
                    expect_sel = (sp // 100) * 100
                    expect_mak = (op // 100) * 100 if op > expect_sel else expect_sel
                    if m_sel and int(m_sel.group(1)) != expect_sel:
                        price_issues.append(f"11st판매가 {m_sel.group(1)}≠{expect_sel}")
                    if m_mak and int(m_mak.group(1)) != expect_mak:
                        price_issues.append(f"11st정상가 {m_mak.group(1)}≠{expect_mak}")
            except Exception as e:
                price_issues.append(f"11st단언EXC:{e}")
            line["price"] = price_issues
            if price_issues:
                overall_ok = False

            # 출력
            c = "OK" if not issues else ",".join(issues)
            t_fail = [f"{k}={v}" for k, v in line["transform"].items() if v != "OK"]
            print(
                f"[{i}/10] {brand} #{goods_no} | 수집:{c} | "
                f"변환실패:{t_fail if t_fail else '없음'}"
            )
        except Exception as e:
            line["collect"] = [f"FATAL: {e}"]
            overall_ok = False
            traceback.print_exc()
            print(f"[{i}/10] {brand}: FATAL {e}")
        report.append(line)
        await asyncio.sleep(0.5)

    print("\n==================== 요약 ====================")
    size_chart_hits = 0
    for r in report:
        sc = r.get("size_chart_len", 0)
        if sc:
            size_chart_hits += 1
        print(
            f"{r['brand']:8s} #{r['goods_no']}: "
            f"수집={'OK' if not r['collect'] else r['collect']} "
            f"변환={[k for k, v in r['transform'].items() if v != 'OK'] or 'ALL_OK'} "
            f"가격={'OK' if not r.get('price') else r['price']} "
            f"실측표={'있음(' + str(sc) + 'B)' if sc else '없음'}"
        )
    print(f"\n실측표 수집된 상품: {size_chart_hits}/10")
    print(f"전체결과: {'✅ 모두 통과' if overall_ok else '❌ 문제 있음'}")
    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
