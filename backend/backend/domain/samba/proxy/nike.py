"""Nike 소싱 클라이언트 - 상품 검색/상세 조회.

사이트: https://www.nike.com/kr
수집 방식:
  - 검색: __NEXT_DATA__ → props.pageProps.initialState.Wall.productGroupings
  - 상세: PDP 직접 fetch → props.pageProps.selectedProduct + contentImages
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote

import httpx

from backend.utils.logger import logger

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "ko-KR,ko;q=0.9",
}

# productType → 한국어 카테고리 매핑
CAT_MAP = {
    "FOOTWEAR": "신발",
    "APPAREL": "의류",
    "ACCESSORIES": "액세서리",
    "EQUIPMENT": "장비",
}

# subTitle 성별 접두사 (길이 내림차순 매칭)
_GENDER_PREFIXES = [
    "주니어(남아)",
    "주니어(여아)",
    "리틀키즈",
    "남성용",
    "여성용",
    "주니어",
    "남성",
    "여성",
    "여아",
    "유아",
    "키즈",
]
# 성별 정규화
_GENDER_NORM = {
    "남성용": "남성",
    "여성용": "여성",
    "주니어(남아)": "주니어",
    "주니어(여아)": "주니어",
    "여아": "주니어",
    "리틀키즈": "키즈",
    "유아": "키즈",
}

# subTitle 끝 키워드 → 세부 카테고리 매핑
SUBTITLE_CAT_KEYWORDS: dict[str, str] = {
    # 신발
    "러닝화": "러닝화",
    "레이싱화": "러닝화",
    "축구화": "축구화",
    "농구화": "농구화",
    "테니스화": "테니스화",
    "골프화": "골프화",
    "슬라이드": "슬라이드",
    "샌들": "슬라이드",
    "신발": "신발",
    # 상의
    "티셔츠": "티셔츠",
    "탑": "티셔츠",
    "탱크": "티셔츠",
    "저지": "저지",
    "폴로": "폴로",
    "셔츠": "셔츠",
    "후디": "후디",
    "후드 재킷": "후디",
    "후드 미드 레이어": "후디",
    "스웻셔츠": "후디",
    "크루": "맨투맨",
    # 아우터
    "재킷": "재킷",
    "파카": "재킷",
    "패딩 재킷": "패딩",
    "패딩 조끼": "패딩",
    "조끼": "패딩",
    # 하의
    "팬츠": "팬츠",
    "조거": "팬츠",
    "쇼츠": "쇼츠",
    "드레스": "드레스",
    "스커트": "스커트",
    "스코트": "스커트",
    # 용품
    "백팩": "가방",
    "캡": "모자",
    "삭스": "양말",
}
# 긴 키워드부터 매칭 (패딩 재킷 > 재킷)
_SORTED_CAT_KEYWORDS = sorted(SUBTITLE_CAT_KEYWORDS.keys(), key=len, reverse=True)
# contains 매칭용 (백팩 세트, 삭스 등)
_CONTAINS_KEYWORDS = ["백팩", "삭스"]


def parse_subtitle(subtitle: str, product_type: str = "") -> tuple[str, str]:
    """Nike subTitle → (성별, 세부카테고리) 추출.

    예: "남성 로드 러닝화" → ("남성", "러닝화")
        "천연 잔디 클리트 축구화" → ("", "축구화")
        "여성 오버사이즈 로고 후디(플러스 사이즈)" → ("여성", "후디")
    """
    if not subtitle:
        return ("", "")

    # 괄호 내용 제거: (와이드), (플러스 사이즈), (21L) 등
    clean = re.sub(r"\([^)]*\)", "", subtitle).strip()

    # 1. 성별 추출
    gender = ""
    rest = clean
    for g in _GENDER_PREFIXES:
        if clean.startswith(g + " ") or clean == g:
            gender = g
            rest = clean[len(g) :].strip()
            break
    gender = _GENDER_NORM.get(gender, gender)

    # "나이키" 브랜드 접두사 제거
    for prefix in ("나이키 ", "나이키"):
        if rest.startswith(prefix):
            rest = rest[len(prefix) :].strip()
            break

    # 2. 끝 키워드 매칭 (긴 키워드 우선)
    for kw in _SORTED_CAT_KEYWORDS:
        if rest.endswith(kw):
            return (gender, SUBTITLE_CAT_KEYWORDS[kw])

    # 3. 포함 매칭 (백팩 세트 등)
    for kw in _CONTAINS_KEYWORDS:
        if kw in rest:
            return (gender, SUBTITLE_CAT_KEYWORDS[kw])

    # 4. fallback: productType 대분류
    return (gender, CAT_MAP.get(product_type, product_type))


# 나이키 공식 취급주의 안내 (출처: https://www.nike.com/kr/help/a/product-handling)
NIKE_CARE_INSTRUCTIONS: dict[str, str] = {
    "신발": (
        "에어솔: 신발의 에어백은 신발 본체와 일체형으로 제작, 교체나 때움 등의 수리가 불가능하여 외력에 의해 에어가 손상된 경우는 보상 처리되지 않습니다. "
        "신발에 기름이 접촉하지 않도록 신경 써주시기 바랍니다. "
        "천연 가죽이나 천은 물기 및 마찰에 의해 색깔이 변할 가능성이 있습니다. "
        "젖은 노면 혹은 미끄러지기 쉬운 장소에서는 주의 바랍니다. "
        "염분(바닷물)이 있는 곳에서 착용하시면 제품이 쉽게 부식됩니다. "
        "고온 다습한 장소에 장시간 방치를 삼가 바랍니다. "
        "천연 가죽 신발은 신발 고정대나 신문지 등으로 형태를 고정하여 보관할 것을 권합니다. "
        "불이나 난방기구 근처에는 보관하지 말아주시기 바랍니다. "
        "신발 뒤꿈치를 꺾어 신지 마시고, 신발끈은 꽉 조여 주시기 바랍니다. "
        "성인, 청소년이 아동용 제품을 착용하면 부상을 입을 수 있습니다. "
        "착화 전 발톱이 길거나 짧으면 운동 및 보행 시 부상의 위험이 있으니 주의하시기 바랍니다. "
        "등산화, 축구화, 야구화의 경우 해당 운동에 맞는 양말을 착용하시기 바랍니다. "
        "고어텍스 제품의 경우 보온효과로 인해 땀이 생길 수 있으므로 주의하시기 바랍니다. "
        "바닥 마모가 심한 경우 미끄러울 수 있으니 무리한 착화를 피하시기 바랍니다. "
        "세탁이 가능한 제품에 한하여 단독 세탁하시고, 세탁 가능 여부는 상품 택을 참조하십시오. "
        "세탁 시 연성 세제 및 상온수(15-25도)를 사용하시기 바랍니다. "
        "세척 시 세탁기에 절대 넣지 마시기 바랍니다. "
        "세제는 중성세제를 사용하시고, 표백제나 표백 성분이 있는 성분은 사용 삼가 바랍니다. "
        "세제를 사용하는 경우에는 세제에 넣은 채로 장시간 방치하지 마세요. "
        "충분하게 물로 헹구어 세제가 남지 않도록 하며 신발끈은 빼서 별도로 손세탁 바랍니다. "
        "벤젠, 신나 등의 휘발성 용제를 사용하면 변형, 변색의 원인이 되므로 삼가 주십시오. "
        "신발이 젖은 경우에는 건조된 천 등으로 물기를 닦아 주십시오. 형태 변형 방지를 위하여 흰 종이나 천을 넣어서, 통풍이 잘 되는 그늘에서 말려 주시고 화기를 피해주십시오. "
        "스토브나 헤어드라이어 등으로 강제 건조할 경우 신발이 변형될 수 있으니 삼가 바랍니다. "
        "천연가죽 세탁: 물세탁을 피하시고, 부드러운 솔이나 젖은 헝겊 또는 신발 전용 클리너로 표면을 닦아서 관리 바랍니다. "
        "인조가죽 세탁: 부드러운 솔로 오염제거(신발끈과 깔창 분리), 비눗물로 표면 세척(끈과 깔창은 중성세제로 세탁하여 통풍이 잘 되는 곳에서 건조), 강한 직사광선에 놓이면 뒤틀리거나 변색될 우려가 있으니 조심하시기 바랍니다. "
        "스웨이드(세무, 스프린터): 물과 접촉 시 물 빠짐 현상이 가속화됩니다. 빨랫비누 및 물 세탁 삼가 바랍니다."
    ),
    "의류": (
        "세탁 시 올이 튀는 현상이 발생할 수 있으니 반드시 세탁망을 사용 바랍니다. "
        "제품 특성에 따라 올이 튀는 현상이 발생할 수 있으니 주의하시기 바랍니다. "
        "세탁 시 반드시 단독 세탁 바랍니다. 그렇지 않을 경우, 염색 잔료가 빠져나와 다른 제품의 이염이 발생할 수 있습니다. "
        "손목과 목 부분의 RIB이 늘어날 수 있으므로 착용 시 과다하게 잡아당기지 마시기 바랍니다. "
        "심한 마찰로 인해 보풀 현상이 발생할 수 있으니 주의하여 착용하시기 바랍니다. "
        "티셔츠 제품의 경우, 편직의 특성상 제품의 하자가 아닙니다. "
        "땀으로 인한 부분 탈색이 발생할 수 있으니 제품이 땀으로 젖었을 시 즉시 세탁하여 주시기 바랍니다. "
        "소비자 부주의에 의한 제품 훼손 및 세탁 잘못에 의한 변형, 품질 보증기간(1년)이 경과한 제품의 품질 이상에 대해서는 보상의 책임을 지지 않으며 수선 가능 시 실비로 수선해 드립니다."
    ),
    "용품": (
        "일부 제품은 세탁이 불가합니다. "
        "필요시 천을 사용하여 닦아주십시오. "
        "표백제 및 강력 효소제는 절대 사용하지 마십시오. "
        "필요시, 옷걸이에 걸어 건조해 주십시오. "
        "다림질을 하지 마십시오. "
        "드라이클리닝을 하지 마십시오. "
        "사용 전 취급 주의사항을 보고 사용하여 주십시오."
    ),
}


class NikeClient:
    """Nike KR 소싱 클라이언트."""

    SEARCH_URL = "https://www.nike.com/kr/w"
    # PDP: styleColor만으로 접근 시 올바른 URL로 리다이렉트됨
    PDP_URL = "https://www.nike.com/kr/t/-/-/{style_color}"

    CHANNEL_ID = "d9a5bc42-4b9c-4976-858a-f159cf99c647"
    PAGE_API_URL = (
        "https://api.nike.com/discover/product_wall/v1/marketplace/KR/language/ko"
        "/consumerChannelId/d9a5bc42-4b9c-4976-858a-f159cf99c647"
    )
    # Nike API는 count 값으로 24/50/100만 허용 (그 외 400 에러)
    PAGE_SIZE = 100

    async def search(
        self, keyword: str, page: int = 1, max_count: int = 500
    ) -> dict[str, Any]:
        """키워드 검색 — 1페이지: HTML __NEXT_DATA__ 파싱, 2페이지~: Nike API 호출.

        nike-api-caller-id 헤더값이 핵심:
          com.nike.commerce.nikedotcom.web  (실제 사이트가 사용하는 값)
        """
        import asyncio

        products: list[dict[str, Any]] = []
        total_resources = 0
        last_error = ""

        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            # 1단계: 첫 페이지 HTML 파싱
            params: dict[str, Any] = {"q": keyword}
            resp = await client.get(self.SEARCH_URL, params=params, headers=HEADERS)
            resp.raise_for_status()
            products, total_resources, first_groupings = (
                self._parse_search_data_with_total(resp.text)
            )
            logger.info(
                f"[Nike] 검색 '{keyword}' 1페이지 → {len(products)}건 (전체 {total_resources}건)"
            )

            if not products or total_resources <= first_groupings:
                return {
                    "products": products[:max_count],
                    "total": total_resources,
                    "last_error": last_error,
                }

            # 리다이렉트 후 실제 URL로 Referer 설정
            final_url = str(resp.url)
            encoded_keyword = quote(keyword, safe="")

            # 2단계: 추가 페이지를 API로 수집
            # anchor는 1페이지 실제 groupings 수부터 시작 (HTML은 항상 24개 반환)
            anchor = first_groupings
            while len(products) < max_count and anchor < total_resources:
                page_url = (
                    f"{self.PAGE_API_URL}"
                    f"?path=/kr/w?q%3D{encoded_keyword}"
                    f"&searchTerms={encoded_keyword}"
                    f"&queryType=PRODUCTS"
                    f"&anchor={anchor}"
                    f"&count={self.PAGE_SIZE}"
                )
                try:
                    api_resp = await client.get(
                        page_url,
                        headers={
                            **HEADERS,
                            "Accept": "application/json",
                            "Referer": final_url,
                            "Origin": "https://www.nike.com",
                            # 실제 Nike 웹사이트가 사용하는 caller-id (이전값 com.nike.web.product-wall.client 는 오작동)
                            "nike-api-caller-id": "com.nike.commerce.nikedotcom.web",
                        },
                    )
                    if api_resp.status_code != 200:
                        last_error = f"HTTP {api_resp.status_code} at anchor={anchor}"
                        logger.warning(f"[Nike] {last_error}: {api_resp.text[:200]}")
                        break
                    data = api_resp.json()
                    # API 응답에서 totalResources 갱신 (더 정확)
                    pages_info = data.get("pages") or {}
                    if pages_info.get("totalResources"):
                        total_resources = pages_info["totalResources"]
                    page_groupings = data.get("productGroupings", [])
                    page_products = self._parse_api_groupings(page_groupings)
                    if not page_products:
                        last_error = f"빈 productGroupings at anchor={anchor}"
                        logger.info(f"[Nike] {last_error}")
                        break
                    # 중복 제거
                    seen = {p["site_product_id"] for p in products}
                    new_items = [
                        p for p in page_products if p["site_product_id"] not in seen
                    ]
                    products.extend(new_items)
                    logger.info(
                        f"[Nike] anchor={anchor} → +{len(new_items)}건 (누적 {len(products)}건)"
                    )
                    # API 응답의 pages.next 가 빈 문자열이면 마지막 페이지
                    if pages_info.get("next") == "":
                        break
                except Exception as e:
                    last_error = f"{type(e).__name__} at anchor={anchor}: {e}"
                    logger.warning(f"[Nike] 페이지 수집 실패 {last_error}")
                    break
                # anchor는 실제 반환된 groupings 수만큼 증가
                anchor += len(page_groupings) if page_groupings else self.PAGE_SIZE
                await asyncio.sleep(0.2)

        products = products[:max_count]
        logger.info(f"[Nike] 검색 '{keyword}' 최종 {len(products)}건 수집")
        return {
            "products": products,
            "total": total_resources,
            "last_error": last_error,
        }

    async def scan_categories(self, keyword: str) -> dict[str, Any]:
        """키워드 검색 후 카테고리 분포 집계.

        subTitle에서 성별+세부카테고리를 추출하여 세분화된 카테고리를 집계.
        전체 상품을 수집하여 정확한 분포를 제공한다.

        Returns:
          {"categories": [...], "total": int, "groupCount": int}
        """
        result = await self.search(keyword, max_count=9999)
        products = result.get("products", [])
        if not products:
            return {"categories": [], "total": 0, "groupCount": 0}

        # 색상별(site_product_id) 기준 카운트 — 컬러웨이 각각 1건으로 집계
        cat_counter: dict[str, int] = {}
        for p in products:
            c1 = p.get("category1", "")
            c2 = p.get("category2", "")
            c3 = p.get("category3", "")
            if not c1 and not c2 and not c3:
                continue
            path = " > ".join([x for x in [c1, c2, c3] if x])
            # categoryCode: "성별_세분류" (그룹 생성 시 고유키로 사용)
            code = "_".join([x for x in [c2, c3] if x]) or c1
            key = f"{code}||{path}||{c1}||{c2}||{c3}"
            cat_counter[key] = cat_counter.get(key, 0) + 1

        categories = []
        for key, count in sorted(cat_counter.items(), key=lambda x: -x[1]):
            code, path, c1, c2, c3 = key.split("||")
            categories.append(
                {
                    "categoryCode": code,
                    "path": path,
                    "count": count,
                    "category1": c1,
                    "category2": c2,
                    "category3": c3,
                }
            )

        total = sum(c["count"] for c in categories)
        logger.info(
            f"[Nike] 카테고리 스캔 완료: '{keyword}' "
            f"→ {len(categories)}개 카테고리, 총 {total}건"
        )
        return {
            "categories": categories,
            "total": total,
            "groupCount": len(categories),
        }

    async def get_detail(
        self,
        style_color: str,
        pdp_url: str | None = None,
        base_info: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """상품 상세 조회 — PDP URL이 있으면 바로 fetch, 없으면 검색 후 fetch.

        pdp_url: 검색 결과에서 이미 얻은 PDP URL (있으면 검색 스킵)
        base_info: 검색 결과 상품 데이터 (이름/가격 보충용)
        """
        _base = base_info or {}

        if not pdp_url:
            # 검색으로 PDP URL + 기본 정보 확인 (pdp_url 없을 때만)
            search_result = await self.search(style_color, max_count=24)
            products = search_result.get("products", [])
            for p in products:
                if p.get("site_product_id") == style_color:
                    pdp_url = p.get("url")
                    _base = p
                    break
            # exact 매칭 못 찾으면 다른 색상 PDP 잡지 말 것 (false sold_out 방지).
            # 검색 결과에 없으면 단종/검색 인덱스 누락 → search_not_found 시그널 전파.
            if not pdp_url:
                return {"error": "search_not_found"}

        # PDP 직접 fetch → 상세 정보 (이미지, 색상, 제조국)
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            resp = await client.get(pdp_url, headers=HEADERS)
            resp.raise_for_status()
            detail = self._parse_pdp_data(resp.text, style_color)
            if not detail:
                return {"error": f"상품 {style_color}를 파싱할 수 없습니다."}

        # 검색 기본 정보 + PDP 상세 정보 병합
        result = {**detail}
        if _base.get("name"):
            result["name"] = _base["name"]
        if _base.get("sale_price") and not result.get("sale_price"):
            result["sale_price"] = _base["sale_price"]
        if _base.get("original_price") and not result.get("original_price"):
            result["original_price"] = _base["original_price"]

        # threads API로 사이즈별 실재고 반영
        try:
            avail = await self._fetch_availability(style_color)
            if avail:
                for opt in result.get("options", []):
                    gtin = opt.get("gtin", "")
                    if gtin and gtin not in avail:
                        opt["stock"] = 0
                    elif gtin and not avail.get(gtin, False):
                        opt["stock"] = 0
        except Exception as e:
            logger.warning(f"[Nike] 재고 API 실패 {style_color}: {e}")

        # Threads API 반영 후 품절 판정: 모든 옵션 stock=0이면 sold_out
        all_options = result.get("options", [])
        if all_options and all(opt.get("stock", 0) <= 0 for opt in all_options):
            result["sale_status"] = "sold_out"
        elif not all_options:
            result["sale_status"] = "sold_out"
        else:
            result["sale_status"] = "in_stock"

        logger.info(
            f"[Nike] 상세 '{style_color}' → 이미지 {len(result.get('images', []))}장"
        )
        return result

    async def _fetch_availability(self, style_color: str) -> dict[str, bool]:
        """threads API로 사이즈별 재고 조회 → {gtin: available} 맵 반환."""
        url = (
            "https://api.nike.com/product_feed/threads/v3"
            "?filter=marketplace(KR)"
            "&filter=language(ko)"
            "&filter=channelId(d9a5bc42-4b9c-4976-858a-f159cf99c647)"
            f"&filter=productInfo.merchProduct.styleColor({style_color})"
        )
        api_headers = {**HEADERS, "Accept": "application/json"}
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers=api_headers)
            if resp.status_code != 200:
                return {}
            data = resp.json()
        objects = data.get("objects", [])
        if not objects:
            return {}
        pi = objects[0].get("productInfo", [{}])[0]
        available_gtins = pi.get("availableGtins", [])
        return {ag["gtin"]: ag.get("available", False) for ag in available_gtins}

    @staticmethod
    def _parse_search_data_with_total(
        html: str,
    ) -> tuple[list[dict[str, Any]], int, int]:
        """검색 페이지 __NEXT_DATA__ → (상품 목록, 총 groupings 수, 1페이지 groupings 수) 반환."""
        nd_match = re.search(
            r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL
        )
        if not nd_match:
            return [], 0, 0

        try:
            nd = json.loads(nd_match.group(1))
        except json.JSONDecodeError:
            return [], 0, 0

        wall = (
            nd.get("props", {})
            .get("pageProps", {})
            .get("initialState", {})
            .get("Wall", {})
        )
        total_resources = wall.get("pageData", {}).get("totalResources", 0)
        groupings = wall.get("productGroupings", [])
        products = NikeClient._parse_api_groupings(groupings)
        return products, total_resources, len(groupings)

    @staticmethod
    def _parse_api_groupings(groupings: list[dict]) -> list[dict[str, Any]]:
        """productGroupings 배열 → 상품 목록 추출 (HTML 파싱 결과 및 API 응답 공용)."""
        products = []
        for group in groupings:
            for prod in group.get("products", []):
                copy = prod.get("copy") or {}
                prices = prod.get("prices") or {}
                images = prod.get("colorwayImages") or {}
                display_colors = prod.get("displayColors") or {}
                pdp_url = prod.get("pdpUrl") or {}

                title = copy.get("title", "")
                subtitle = copy.get("subTitle", "")
                name = f"{title} {subtitle}".strip() if subtitle else title

                current_price = prices.get("currentPrice", 0)
                initial_price = prices.get("initialPrice", 0)
                img_url = images.get("portraitURL", "") or images.get("squarishURL", "")
                product_code = prod.get("productCode", "")
                group_key = prod.get("groupKey", product_code)
                product_type = prod.get("productType", "")
                color = display_colors.get("colorDescription", "")
                url = pdp_url.get("url", "") if isinstance(pdp_url, dict) else ""

                # subTitle 기반 세분류: 성별 + 카테고리
                gender, sub_cat = parse_subtitle(subtitle, product_type)
                # category 경로: "남성 > 러닝화" (Nike 제외 — source_site로 충분)
                cat_parts = [x for x in [gender, sub_cat] if x]
                category_path = " > ".join(cat_parts) if cat_parts else "Nike"

                products.append(
                    {
                        "site_product_id": product_code,
                        "group_key": group_key or product_code,
                        "name": name or f"Nike {product_code}",
                        "original_price": initial_price,
                        "sale_price": current_price or initial_price,
                        "images": [img_url] if img_url else [],
                        "brand": "Nike",
                        "source_site": "Nike",
                        "source_url": url,
                        "category": category_path,
                        "category1": "Nike",
                        "category2": gender,
                        "category3": sub_cat,
                        "color": color,
                        "video_url": url,
                        "url": url,
                        "options": [],
                        "detail_html": "",
                        "free_shipping": True,
                    }
                )

        return products

    @staticmethod
    def _parse_pdp_data(html: str, style_color: str) -> dict[str, Any] | None:
        """PDP 페이지 __NEXT_DATA__ → 상세 정보 추출.

        props.pageProps.selectedProduct 에서:
        - title, subtitle (h1/h2 텍스트로 보완)
        - prices
        - contentImages (최대 8장)
        - colorDescription
        - manufacturingCountriesOfOrigin

        HTML에서:
        - 사이즈 라디오 버튼 label → options
        """
        nd_match = re.search(
            r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL
        )
        if not nd_match:
            return None

        try:
            nd = json.loads(nd_match.group(1))
        except json.JSONDecodeError:
            return None

        page_props = nd.get("props", {}).get("pageProps", {})
        sp = page_props.get("selectedProduct") or {}

        # productGroups에서 해당 styleColor 상품 데이터 추출
        product_groups = page_props.get("productGroups") or []
        prod_data: dict[str, Any] = {}
        if product_groups:
            products_map = product_groups[0].get("products") or {}
            prod_data = products_map.get(style_color) or {}

        # 제목: selectedProduct → productGroups → h1 순으로 fallback
        title = sp.get("title", "")
        subtitle = sp.get("subtitle", "")
        if not title:
            title = prod_data.get("title", "") or sp.get("groupKey", "")
        if not title:
            h1_match = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.DOTALL)
            if h1_match:
                title = re.sub(r"<[^>]+>", "", h1_match.group(1)).strip()

        name = f"{title} {subtitle}".strip() if subtitle else title

        # 가격
        prices = sp.get("prices") or prod_data.get("prices") or {}
        current_price = prices.get("currentPrice", 0)
        initial_price = prices.get("initialPrice", 0)

        # 이미지: contentImages (갤러리) — selectedProduct 또는 prod_data
        content_images = sp.get("contentImages") or prod_data.get("contentImages") or []
        images = []
        for ci in content_images:
            props = ci.get("properties") or {}
            url = (props.get("squarish") or {}).get("url") or (
                props.get("portrait") or {}
            ).get("url")
            if url:
                # Nike CDN: t_default → 팔레트 PNG 반환 → strip_exif에서 검정색으로 깨짐
                # t_PDP_1280_v1 프리셋 사용 → JPEG 반환 → 정상 업로드
                url = url.replace("/t_default/", "/t_PDP_1280_v1/")
                if url not in images:
                    images.append(url)

        # 색상/제조국
        color = sp.get("colorDescription", "") or prod_data.get("colorDescription", "")
        origin_list = (
            sp.get("manufacturingCountriesOfOrigin")
            or prod_data.get("manufacturingCountriesOfOrigin")
            or []
        )
        origin = ", ".join(origin_list) if origin_list else ""

        # 카테고리/성별
        product_type = sp.get("productType", "") or prod_data.get("productType", "")
        category1 = CAT_MAP.get(product_type, product_type)
        # subtitle 기반 세분류 (scan_products_from_search와 동일 로직)
        _sub_gender, _sub_cat = parse_subtitle(subtitle, product_type)
        # parse_subtitle 실패 시 CAT_MAP fallback
        _final_cat = _sub_cat or category1

        # taxonomyLabels.Gender → 이미 한국어로 제공 ("남성"/"여성"/"키즈" 등)
        taxonomy = prod_data.get("taxonomyLabels") or sp.get("taxonomyLabels") or {}
        gender_labels = taxonomy.get("Gender") or []
        if gender_labels:
            gender_kr = gender_labels[0]
        else:
            # fallback: genders 영문 코드 → 한국어 변환
            gender_en = (sp.get("genders") or prod_data.get("genders") or [""])[0]
            gender_map = {
                "WOMEN": "여성",
                "MEN": "남성",
                "UNISEX": "공용",
                "KIDS": "키즈",
            }
            gender_kr = gender_map.get(gender_en, gender_en)

        # 사이즈 옵션: productGroups.sizes → localizedLabel + GTIN + status
        # prod_data.sizes 가 비면 selectedProduct.sizes 폴백 (PDP styleColor mismatch 안전망).
        sizes_data = prod_data.get("sizes") or sp.get("sizes") or []
        if sizes_data:
            options = []
            for s in sizes_data:
                label = s.get("localizedLabel") or s.get("label", "")
                if not label:
                    continue
                # GTIN 추출 (threads API 재고 매칭용)
                gtin_list = s.get("gtins") or []
                gtin = gtin_list[0].get("gtin", "") if gtin_list else ""
                options.append(
                    {
                        "name": label,
                        "size": label,
                        "gtin": gtin,
                        "stock": 99 if s.get("status") == "ACTIVE" else 0,
                        "us_label": s.get("label", ""),  # US 사이즈 (예: "M 5.5")
                    }
                )
        else:
            # fallback: HTML label 태그에서 파싱
            size_labels = re.findall(r"<label[^>]*>\s*(\d{3})\s*</label>", html)
            options = [
                {"name": s, "size": s, "stock": 99} for s in dict.fromkeys(size_labels)
            ]

        # 상품 정보 섹션 (featuresAndBenefits, productDetails)
        product_info = prod_data.get("productInfo") or {}
        features = product_info.get("featuresAndBenefits") or []
        details = product_info.get("productDetails") or []

        # material: productDetails body 중 '%' 포함 항목만 (소재 비율)
        # ex) "100% 면", "나일론 80% / 폴리에스터 20%"
        material_lines = []
        for section in details:
            for item in section.get("body") or []:
                if "%" in item:
                    material_lines.append(item)
        material = ", ".join(material_lines) if material_lines else ""

        # 품번 (selectedProduct 또는 productGroups에서 styleCode)
        style_code = (
            sp.get("styleCode")
            or prod_data.get("styleCode")
            or (style_color.split("-")[0] if "-" in style_color else style_color)
        )

        # moreInfo: 고시정보 HTML 배열 파싱
        # moreInfo[0]: 제조연월, moreInfo[1]: A/S·세탁방법·품질보증, moreInfo[2]: 제조자/수입자
        more_info = prod_data.get("moreInfo") or sp.get("moreInfo") or []

        def _li_texts(html_parts: list) -> list[str]:
            """HTML 파트 목록에서 li 텍스트(태그 제거) 추출."""
            combined = "".join(
                html_parts if isinstance(html_parts, list) else [str(html_parts)]
            )
            items = re.findall(r"<li>(.*?)</li>", combined, re.DOTALL)
            return [re.sub(r"<[^>]+>", "", it).strip() for it in items]

        # 제조사: moreInfo[2] → "제조자/수입자" li에서 추출
        manufacturer = "Nike Inc / (유)나이키코리아"  # 기본값
        if len(more_info) > 2:
            for item in _li_texts(more_info[2]):
                if "수입자" in item or "제조자" in item:
                    manufacturer = (
                        item.split("표기:")[-1].strip() if "표기:" in item else item
                    )
                    break

        # 세탁방법 / 품질보증: moreInfo[1] → 각 li 시작 키워드로 분류
        care_instructions = ""
        quality_guarantee = ""
        if len(more_info) > 1:
            for item in _li_texts(more_info[1]):
                if item.startswith("세탁방법") and not care_instructions:
                    care_instructions = item
                elif item.startswith("품질보증") and not quality_guarantee:
                    quality_guarantee = item

        # care_instructions 정리: 유니코드 줄바꿈 제거
        care_instructions = (
            care_instructions.replace("\u2028", " ").replace("\u2029", " ").strip()
        )

        # care_instructions가 비어있거나 '자세히 보기' 링크 참조만 있는 경우 → 공식 안내 전문으로 대체
        if not care_instructions or "자세히 보기" in care_instructions:
            care_key = CAT_MAP.get(product_type.upper(), "")
            if not care_key:
                # productType 없을 때 category1(이미 한국어)로 fallback
                cat1_lower = category1.lower()
                if (
                    "신발" in cat1_lower
                    or "슈즈" in cat1_lower
                    or "부츠" in cat1_lower
                    or "샌들" in cat1_lower
                    or "슬라이드" in cat1_lower
                ):
                    care_key = "신발"
                elif (
                    "의류" in cat1_lower
                    or "티셔츠" in cat1_lower
                    or "팬츠" in cat1_lower
                    or "재킷" in cat1_lower
                    or "후드" in cat1_lower
                ):
                    care_key = "의류"
                else:
                    care_key = "용품"
            care_instructions = NIKE_CARE_INSTRUCTIONS.get(care_key, "")

        # PDP URL (원문링크): pdpUrl.url 우선, productInfo.url fallback
        pdp_url_obj = prod_data.get("pdpUrl") or {}
        video_url = (
            (pdp_url_obj.get("url") if isinstance(pdp_url_obj, dict) else "")
            or product_info.get("url")
            or ""
        )

        # detail_html: 상품설명 + 슬로건 + 상품특징 + 상품상세
        html_parts = []
        product_description = product_info.get("productDescription") or ""
        reason_to_buy = product_info.get("reasonToBuy") or ""
        if product_description:
            html_parts.append(f"<p>{product_description}</p>")
        if reason_to_buy:
            html_parts.append(f"<p><em>{reason_to_buy}</em></p>")
        for section in features + details:
            header = section.get("header", "")
            body = section.get("body") or []
            if header or body:
                items_html = "".join(f"<li>{item}</li>" for item in body)
                html_parts.append(f"<h3>{header}</h3><ul>{items_html}</ul>")
        detail_html = "".join(html_parts)

        return {
            "site_product_id": style_color,
            "name": name or f"Nike {style_color}",
            "original_price": initial_price,
            "sale_price": current_price or initial_price,
            "images": images,
            "brand": "Nike",
            "source_site": "Nike",
            "category": " > ".join([x for x in [gender_kr, _final_cat] if x]) or "Nike",
            "category1": "Nike",
            "category2": gender_kr,
            "category3": _final_cat,
            "source_url": video_url,
            "color": color,
            "origin": origin,
            "material": material,
            "style_code": style_code,
            "sex": gender_kr,
            "manufacturer": manufacturer,
            "care_instructions": care_instructions,
            "quality_guarantee": quality_guarantee,
            "video_url": video_url,
            "options": options,
            "detail_html": detail_html,
            "free_shipping": True,
        }
