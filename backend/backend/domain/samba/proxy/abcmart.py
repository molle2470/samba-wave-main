"""ABC마트 / 그랜드스테이지 소싱용 클라이언트 - httpx 기반.

두 사이트는 동일 도메인(www.a-rt.com)을 공유하며 channel 파라미터로 구분된다.
  - ABC마트:     channel=10001
  - 그랜드스테이지: channel=10002 / tChnnlNo=10002

검색 API (내부):
  GET /display/search-word/result-total/list
  파라미터: searchWord, page, perPage, sort, channel, pageColumn

상세 URL (신형 /product/new 사용 — 구형 /product는 최대혜택가 미반영):
  https://abcmart.a-rt.com/product/new?prdtNo={product_id}
  https://grandstage.a-rt.com/product/new?prdtNo={product_id}&tChnnlNo=10002
"""

from __future__ import annotations

import asyncio
import json
import math
import re
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import quote

import httpx

from backend.utils.logger import logger


class RateLimitError(Exception):
    """a-rt.com 차단 감지 (429/403)."""

    def __init__(self, status: int, retry_after: int = 0):
        self.status = status
        self.retry_after = retry_after
        super().__init__(f"HTTP {status} (retry_after={retry_after})")


async def get_abcmart_cookies() -> list[str]:
    """DB에서 ABCmart 로그인 쿠키 목록 조회.

    SambaSettings.abcmart_cookies(JSON 배열) → 만료되지 않은 계정 쿠키들.
    잡 시작 시 1회 호출 → 잡 내내 재사용.
    """
    try:
        import json as _json

        from sqlmodel import select as _sel

        from backend.db.orm import get_read_session
        from backend.domain.samba.forbidden.model import SambaSettings

        async with get_read_session() as session:
            result = await session.execute(
                _sel(SambaSettings).where(SambaSettings.key == "abcmart_cookies")
            )
            row = result.scalar_one_or_none()
            if row and row.value:
                val = (
                    _json.loads(row.value) if isinstance(row.value, str) else row.value
                )
                if isinstance(val, list):
                    return [c for c in val if c]
            return []
    except Exception as e:
        logger.warning(f"[ABCmart] 쿠키 조회 실패: {e}")
        return []


async def prepare_abcmart_cache() -> None:
    """ABCmart 벌크 갱신 전 쿠키 캐싱 (잡 시작 시 1회 호출).

    DB의 abcmart_cookies(만료되지 않은 계정 쿠키 목록) → ARTSourcingClient._bulk_cache.
    이후 모든 _acquire_session_client() 호출이 이 캐시를 사용.
    """
    cookies = await get_abcmart_cookies()
    ARTSourcingClient._bulk_cache = {
        "cookies": cookies,
        "cookie": cookies[0] if cookies else "",
        "idx": 0,
        "expired": False,
        "loaded": True,  # 빈 결과여도 lazy-load 재시도 폭주 방지
    }
    logger.info(f"[ABCmart] 쿠키 캐시 로딩: {len(cookies)}개")


def _mark_abcmart_cookie_expired() -> None:
    """현재 사용 중인 쿠키를 만료로 마킹 (loginYn != Y 응답 시 호출).

    캐시에서만 마킹 — DB에는 다음 sync 때 확장앱이 직접 expired=True로 알림.
    """
    cache = ARTSourcingClient._bulk_cache
    if cache.get("cookie") and not cache.get("expired"):
        cache["expired"] = True
        logger.warning(
            "[ABCmart] 로그인 쿠키 만료 감지 — 익명 폴백으로 전환 (다음 sync까지 보수적 cost)"
        )


class ARTSourcingClient:
    """ABC마트 / 그랜드스테이지 소싱용 웹 스크래핑 클라이언트.

    channel 파라미터로 사이트를 구분한다:
      ARTSourcingClient()              → ABC마트
      ARTSourcingClient("10002")       → 그랜드스테이지
    """

    BASE = "https://www.a-rt.com"
    # 내부 검색 API (SPA가 호출하는 JSON API)
    SEARCH_API_PATH = "/display/search-word/result-total/list"
    DETAIL_PATH = "/product"
    API_FAILURE_COST_FALLBACK = 200_000

    # 잡 단위 쿠키 캐시 (확장앱이 sync한 로그인 쿠키 → 잡 시작 시 1회 로딩)
    # _prepare_abcmart_cache()에서 채움. 키: "cookie"(현재값), "cookies"(전체), "expired"(bool)
    _bulk_cache: dict = {}

    @classmethod
    def set_membership_rate(cls, rate: float) -> None:
        """[Deprecated] 멤버십 rate는 더 이상 캐시하지 않는다.

        이전 설계(rate × sale_price 곱셈)는 "상시 할인 제외 상품"을 구분 못 하는
        구조적 결함이 있었음. 현재는 로그인 쿠키 sync로 API의 alwaysDscntAmt를
        직접 사용하므로 rate 캐시 불필요. 호환성 위해 stub으로 남김.
        """
        logger.info(f"[ABCmart] 멤버십 rate 수신(미사용, deprecated): {rate}%")

    # ABC마트: channel=10001, 그랜드스테이지: channel=10002
    CHANNEL_ABCMART = "10001"
    CHANNEL_GRANDSTAGE = "10002"

    # 채널별 서브도메인 (상품 상세 내부 API 호출용)
    SUBDOMAIN_MAP: dict[str, str] = {
        "10001": "https://abcmart.a-rt.com",
        "10002": "https://grandstage.a-rt.com",
    }

    # genderGbnCode → 성별 매핑 (쿠팡 필터값 기준)
    GENDER_CODE_MAP: dict[str, str] = {
        "10001": "여성용",
        "10002": "남성용",
        "10003": "남여공용",
        "10004": "아동/주니어공용",
        "10005": "남아/주니어용",
        "10006": "여아/주니어용",
    }

    # orgPlaceCode → 원산지명 매핑
    ORIGIN_CODE_MAP: dict[str, str] = {
        "10000": "베트남",
        "10002": "한국",
        "10004": "한국",
        "10006": "한국",
        "10008": "중국",
        "10009": "중국",
        "10010": "한국",
        "10012": "베트남",
        "10014": "독일",
        "10016": "이탈리아",
        "10018": "영국",
        "10020": "미국",
        "10022": "일본",
        "10024": "프랑스",
        "10026": "스페인",
        "10028": "포르투갈",
        "10030": "인도네시아",
        "10032": "캄보디아",
        "10034": "미얀마",
        "10036": "방글라데시",
        "10038": "인도",
        "10040": "스리랑카",
        "10042": "독일",
        "10044": "독일",
        "10015": "인도",
    }

    # 페이지 HTML 요청용 헤더
    HEADERS: dict[str, str] = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Connection": "keep-alive",
        "Referer": "https://www.a-rt.com/",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }

    # 내부 JSON API 요청용 헤더 (Ajax 요청처럼 보이게)
    API_HEADERS: dict[str, str] = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Connection": "keep-alive",
        "Referer": "https://www.a-rt.com/display/search-word/result",
        "X-Requested-With": "XMLHttpRequest",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }

    def __init__(
        self,
        channel: Optional[str] = None,
        *,
        proxy_pool: Optional[list[str]] = None,
    ) -> None:
        """
        Args:
          channel: None → ABC마트(10001), "10002" → 그랜드스테이지
          proxy_pool: Cloud Run IP가 a-rt.com에 차단되는 현상 우회용 프록시 풀
                     라운드로빈으로 순환 사용 (무신사/GSShop과 동일 풀 공유)
        """
        self.channel = channel or self.CHANNEL_ABCMART
        self._source_site = (
            "GrandStage" if channel == self.CHANNEL_GRANDSTAGE else "ABCmart"
        )
        self._timeout = httpx.Timeout(10.0, connect=5.0)
        self._proxy_pool: list[str] = proxy_pool or []
        self._proxy_idx = 0

    def _next_proxy(self) -> Optional[str]:
        """라운드로빈 프록시 선택. 풀이 비어 있으면 None 반환(직접 접속)."""
        if not self._proxy_pool:
            return None
        proxy = self._proxy_pool[self._proxy_idx % len(self._proxy_pool)]
        self._proxy_idx += 1
        return proxy

    # ------------------------------------------------------------------
    # 검색
    # ------------------------------------------------------------------

    async def search_products(
        self,
        keyword: str,
        page: int = 1,
        size: int = 40,
        **filters: Any,
    ) -> list[dict[str, Any]]:
        """a-rt.com 내부 검색 API 직접 호출.

        세션 획득 후 JSON API를 호출한다.
        a-rt.com API는 JSESSIONID 쿠키 없이 호출 시 빈 응답({})을 반환하므로
        동일 세션 내에서 홈 → 검색 페이지 순으로 방문 후 API를 요청해야 한다.

        Args:
          keyword: 검색 키워드
          page: 페이지 번호 (1부터)
          size: 페이지당 결과 수
          **filters: 추가 필터 (미사용)

        Returns:
          표준 상품 dict 리스트

        Raises:
          RateLimitError: 429/403 응답 시
        """
        site_label = f"[{self._source_site}]"
        logger.info(f"{site_label} 검색 시작: '{keyword}' (page={page})")

        subdomain = self.SUBDOMAIN_MAP.get(self.channel, self.SUBDOMAIN_MAP["10001"])
        encoded_kw = quote(keyword)
        search_page_url = (
            f"{subdomain}/display/search-word/result"
            f"?searchWord={encoded_kw}&channel={self.channel}"
        )
        api_url = f"{subdomain}{self.SEARCH_API_PATH}"

        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, follow_redirects=True, proxy=self._next_proxy()
            ) as client:
                # 세션 획득: 홈 방문 → 검색 페이지 방문 (JSESSIONID 쿠키 설정)
                await client.get(subdomain + "/", headers=self.HEADERS)
                await client.get(search_page_url, headers=self.HEADERS)

                # API 호출 (세션 쿠키 자동 포함)
                api_headers = {
                    **self.API_HEADERS,
                    "Referer": search_page_url,
                }
                resp = await client.get(
                    api_url,
                    params={
                        "searchWord": keyword,
                        "page": str(page),
                        "perPage": str(size),
                        "sort": "point",
                        "channel": self.channel,
                        "pageColumn": "3",
                        "tabGubun": "total",
                        "searchPageGubun": "product",
                        "smartSearchCheck": "false",
                    },
                    headers=api_headers,
                )

                if resp.status_code in (429, 403):
                    retry_after = int(resp.headers.get("Retry-After", "60"))
                    logger.warning(f"{site_label} 차단 감지 HTTP {resp.status_code}")
                    raise RateLimitError(resp.status_code, retry_after)

                if resp.status_code != 200:
                    logger.warning(f"{site_label} 검색 API HTTP {resp.status_code}")
                    return []

                data = resp.json()

            products = self._parse_search_api(data, keyword)
            logger.info(f"{site_label} 검색 완료: '{keyword}' → {len(products)}개")
            return products

        except RateLimitError:
            raise
        except httpx.TimeoutException:
            logger.error(f"{site_label} 검색 타임아웃: {keyword}")
            return []
        except Exception as e:
            logger.error(f"{site_label} 검색 실패: {keyword} — {e}")
            return []

    def _parse_search_api(
        self, data: dict[str, Any], keyword: str
    ) -> list[dict[str, Any]]:
        """내부 검색 API JSON 응답 파싱.

        실제 API 응답 형식 (대문자 UPPER_SNAKE_CASE):
          PRDT_NO, PRDT_NAME, BRAND_NAME, PRDT_DC_PRICE, NRMAL_AMT, PRDT_IMAGE_URL, SOLD_OUT
        레거시 camelCase 키도 폴백으로 지원한다.
        """
        products: list[dict[str, Any]] = []
        seen: set[str] = set()

        # API 응답에서 상품 목록 추출 (실제 응답: SEARCH 키, 레거시: productList 등)
        raw_list: list[dict[str, Any]] = (
            data.get("SEARCH")
            or data.get("productList")
            or data.get("products")
            or (data.get("result") or {}).get("productList")
            or (data.get("data") or {}).get("productList")
            or []
        )

        if not raw_list:
            logger.warning(
                f"[{self._source_site}] API 응답에 상품 없음 (keyword={keyword}), 키: {list(data.keys())}"
            )
            return []

        for item in raw_list:
            # 상품 ID (실제: PRDT_NO, 레거시: prdtNo, productNo)
            product_id = str(
                item.get("PRDT_NO")
                or item.get("prdtNo")
                or item.get("productNo")
                or item.get("id")
                or ""
            )
            if not product_id or product_id in seen:
                continue
            seen.add(product_id)

            # 상품명 (실제: PRDT_NAME, 레거시: prdtNm, productName)
            name = (
                item.get("PRDT_NAME")
                or item.get("prdtNm")
                or item.get("productName")
                or item.get("name")
                or ""
            ).strip()

            # 브랜드 (실제: BRAND_NAME, 레거시: brandNm) — 유통사명 방어
            _VENDOR_NAMES_SEARCH = {
                "ABC-MART",
                "ABC마트",
                "ABCmart",
                "GrandStage",
                "그랜드스테이지",
                "GRAND STAGE",
                "debug",  # 비정상 테스트 브랜드명 방어
            }
            brand = (
                item.get("BRAND_NAME") or item.get("brandNm") or item.get("brand") or ""
            ).strip()
            if brand in _VENDOR_NAMES_SEARCH:
                brand = ""

            # 판매가 (실제: PRDT_DC_PRICE, 레거시: sellPrc, salePrice)
            sale_price = self._safe_int(
                item.get("PRDT_DC_PRICE")
                or item.get("sellPrc")
                or item.get("salePrice")
                or item.get("price")
                or 0
            )
            # 정상가 (실제: NRMAL_AMT, 레거시: consumerPrc, orgPrice)
            original_price = self._safe_int(
                item.get("NRMAL_AMT")
                or item.get("consumerPrc")
                or item.get("orgPrice")
                or item.get("originalPrice")
                or sale_price
            )

            # 이미지 (실제: PRDT_IMAGE_URL, 레거시: imgUrl, image)
            img_url = (
                item.get("PRDT_IMAGE_URL")
                or item.get("imgUrl")
                or item.get("image")
                or item.get("thumbnail")
                or ""
            )
            image = self._normalize_image(img_url)

            # 품절 여부 (실제: SOLD_OUT="y"/"n", 레거시: soldOutYn="Y"/"N")
            is_sold_out = bool(
                item.get("SOLD_OUT") == "y"
                or item.get("soldOutYn") == "Y"
                or item.get("isSoldOut")
                or item.get("stockQty") == 0
            )

            if not name and sale_price == 0:
                continue

            products.append(
                {
                    "siteProductId": product_id,
                    "goodsNo": product_id,
                    "name": name or f"상품_{product_id}",
                    "salePrice": sale_price,
                    "originalPrice": original_price,
                    "image": image,
                    "brand": brand,
                    "isSoldOut": is_sold_out,
                    "sourceSite": self._source_site,
                }
            )

        return products

    # ------------------------------------------------------------------
    # worker 호환 래퍼 (snake_case + 페이징 + {"products":[], "total":N})
    # ------------------------------------------------------------------

    @staticmethod
    def _to_snake(item: dict[str, Any]) -> dict[str, Any]:
        """camelCase 상품 dict → worker가 기대하는 snake_case 변환."""
        return {
            "site_product_id": item.get("siteProductId") or item.get("goodsNo", ""),
            "name": item.get("name", ""),
            "sale_price": item.get("salePrice", 0),
            "original_price": item.get("originalPrice", 0),
            "cost": item.get("salePrice", 0),
            "brand": item.get("brand", ""),
            "images": [item["image"]] if item.get("image") else [],
            "source_site": item.get("sourceSite", "ABCmart"),
            "source_url": item.get("sourceUrl", ""),
            "is_sold_out": item.get("isSoldOut", False),
        }

    async def search(
        self, keyword: str, max_count: int = 9999, **kwargs: Any
    ) -> dict[str, Any]:
        """worker 호환 검색 — 전체 페이지 순회 + snake_case + 카테고리 코드 포함."""
        # _search_all_for_scan으로 raw 아이템 수집 (CTGR_CD_ALL 포함)
        raw_items, total_count = await self._search_all_for_scan(keyword)
        if not raw_items:
            return {"products": [], "total": 0}

        # 카테고리 제외 목록
        _CTGR_EXCLUDE = {
            "홈",
            "HOME",
            "ABC마트",
            "그랜드스테이지",
            "GRAND STAGE",
            "ABCmart",
        }

        products: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in raw_items:
            pid = str(item.get("PRDT_NO") or item.get("prdtNo") or "")
            if not pid or pid in seen:
                continue
            seen.add(pid)

            name = (item.get("PRDT_NAME") or item.get("prdtNm") or "").strip()
            sale_price = self._safe_int(
                item.get("PRDT_DC_PRICE") or item.get("sellPrc") or 0
            )
            original_price = self._safe_int(
                item.get("NRMAL_AMT") or item.get("consumerPrc") or sale_price
            )
            brand = (item.get("BRAND_NAME") or item.get("brandNm") or "").strip()
            img = self._normalize_image(
                item.get("PRDT_IMAGE_URL") or item.get("imgUrl") or ""
            )
            is_sold_out = item.get("SOLD_OUT") == "y" or item.get("soldOutYn") == "Y"

            # 카테고리 코드/경로 추출 (카테고리 필터링용)
            cat_str = (
                item.get("CTGR_NAME_ALL") or item.get("SY_CTGR_NAME") or ""
            ).strip()
            if "," in cat_str:
                cat_str = cat_str.split(",")[0].strip()
            code_str = (item.get("CTGR_CD_ALL") or item.get("SY_CTGR_NO") or "").strip()
            if "," in code_str:
                code_str = code_str.split(",")[0].strip()
            code_parts = [c.strip() for c in code_str.split(">") if c.strip()]
            cat_code = code_parts[-1] if code_parts else ""
            cat_levels = [
                c.strip()
                for c in cat_str.split(">")
                if c.strip() and c.strip() not in _CTGR_EXCLUDE
            ]

            products.append(
                {
                    "site_product_id": pid,
                    "name": name,
                    "sale_price": sale_price,
                    "original_price": original_price,
                    "cost": sale_price,
                    "brand": brand,
                    "images": [img] if img else [],
                    "source_site": self._source_site,
                    "is_sold_out": is_sold_out,
                    "category_code": cat_code,
                    "category": " > ".join(cat_levels) if cat_levels else "",
                    "category1": cat_levels[0] if len(cat_levels) > 0 else "",
                    "category2": cat_levels[1] if len(cat_levels) > 1 else "",
                    "category3": cat_levels[2] if len(cat_levels) > 2 else "",
                }
            )

        products = products[:max_count]
        logger.info(
            f"[{self._source_site}] search() 완료: '{keyword}' → {len(products)}건"
        )
        return {"products": products, "total": total_count}

    async def get_detail(
        self,
        product_id: str,
        shared_client: Optional[httpx.AsyncClient] = None,
    ) -> dict[str, Any]:
        """worker 호환 상세 조회 — get_product_detail 래핑 + snake_case 변환."""
        detail = await self.get_product_detail(product_id, shared_client=shared_client)
        if not detail:
            return {}
        # get_product_detail은 이미 상세 필드를 포함하므로 키만 snake_case로 변환
        return {
            "site_product_id": detail.get("siteProductId", ""),
            "name": detail.get("name", ""),
            "brand": detail.get("brand", ""),
            "sale_price": detail.get("salePrice", 0),
            "original_price": detail.get("originalPrice", 0),
            "cost": detail.get("bestBenefitPrice", 0) or detail.get("salePrice", 0),
            "bestBenefitPrice": detail.get("bestBenefitPrice", 0),
            "images": detail.get("images", []),
            "options": detail.get("options", []),
            "category": detail.get("category", ""),
            "category1": detail.get("category1", ""),
            "category2": detail.get("category2", ""),
            "category3": detail.get("category3", ""),
            "source_url": detail.get("sourceUrl", ""),
            "source_site": detail.get("sourceSite", "ABCmart"),
            "detail_images": detail.get("detailImages", []),
            "detail_html": detail.get("detailHtml", ""),
            "style_code": detail.get("styleCode", ""),
            "material": detail.get("material", ""),
            "color": detail.get("color", ""),
            "origin": detail.get("origin", ""),
            "sex": detail.get("sex", ""),
            "season": detail.get("season", ""),
            "care_instructions": detail.get("careInstructions", ""),
            "quality_guarantee": detail.get("qualityGuarantee", ""),
            "shipping_fee": detail.get("shippingFee", 0),
            "free_shipping": detail.get("freeShipping", False),
        }

    def _parse_search_html(self, html: str, keyword: str) -> list[dict[str, Any]]:
        """검색 결과 HTML에서 상품 정보 추출.

        방법1: 상품 링크(prdtNo=숫자) + 주변 블록 파싱
        방법2: JSON-LD 폴백
        """
        products: list[dict[str, Any]] = []
        seen: set[str] = set()

        # 방법 1: 상품 카드 블록 파싱 (li 또는 div 단위)
        # a-rt.com 검색 결과는 상품 카드를 li/div로 반복하며, 각 카드에 prdtNo 포함
        block_pattern = re.compile(
            r'<(?:li|div)[^>]*class="[^"]*(?:goods|product|item)[^"]*"[^>]*>(.*?)</(?:li|div)>',
            re.DOTALL | re.IGNORECASE,
        )
        blocks = block_pattern.findall(html)

        # 블록이 없으면 전체 HTML에서 상품 링크 기반 추출
        if not blocks:
            blocks = [html]

        for block in blocks:
            # 상품 ID 추출 (prdtNo=숫자)
            id_match = re.search(r"prdtNo=(\d+)", block)
            if not id_match:
                continue
            product_id = id_match.group(1)
            if product_id in seen:
                continue
            seen.add(product_id)

            # 상품명 추출
            name = self._extract_text(
                block, r'class="[^"]*(?:goods|product|item)[_-]?name[^"]*"[^>]*>([^<]+)'
            )
            if not name:
                name = self._extract_text(block, r'title="([^"]+)"')
            if not name:
                name = self._extract_text(block, r'alt="([^"]+)"')

            # 가격 추출 — 판매가 우선
            sale_price = self._extract_price(
                block, r'class="[^"]*(?:sale|sell)[_-]?price[^"]*"[^>]*>.*?(\d[\d,]+)'
            )
            if sale_price == 0:
                sale_price = self._extract_price(
                    block, r'class="[^"]*price[^"]*"[^>]*>.*?(\d[\d,]+)'
                )

            # 정상가 추출
            original_price = self._extract_price(
                block,
                r'class="[^"]*(?:org|original|old)[_-]?price[^"]*"[^>]*>.*?(\d[\d,]+)',
            )
            if original_price == 0:
                original_price = sale_price

            # 이미지 추출
            img_match = re.search(
                r'<img[^>]+(?:src|data-src|data-lazy)="([^"]+)"',
                block,
                re.IGNORECASE,
            )
            image = self._normalize_image(img_match.group(1) if img_match else "")

            # 브랜드 추출
            brand = self._extract_text(block, r'class="[^"]*brand[^"]*"[^>]*>([^<]+)')

            # 품절 여부
            is_sold_out = bool(
                re.search(r"(?:품절|soldout|sold.out|SOLD\s*OUT)", block, re.IGNORECASE)
            )

            if not name and sale_price == 0:
                continue

            products.append(
                {
                    "siteProductId": product_id,
                    "goodsNo": product_id,
                    "name": name.strip() if name else f"상품_{product_id}",
                    "salePrice": sale_price,
                    "originalPrice": original_price,
                    "image": image,
                    "brand": brand.strip() if brand else "",
                    "isSoldOut": is_sold_out,
                    "sourceSite": self._source_site,
                }
            )

        # 방법 2: 블록 파싱 결과 없으면 전체 HTML에서 ID만 추출 후 기본 정보 구성
        if not products:
            for pid_match in re.finditer(r"prdtNo=(\d+)", html):
                product_id = pid_match.group(1)
                if product_id in seen:
                    continue
                seen.add(product_id)
                products.append(
                    {
                        "siteProductId": product_id,
                        "goodsNo": product_id,
                        "name": "",
                        "salePrice": 0,
                        "originalPrice": 0,
                        "image": "",
                        "brand": "",
                        "isSoldOut": False,
                        "sourceSite": self._source_site,
                    }
                )

        return products

    # ------------------------------------------------------------------
    # 내부 JSON API 직접 호출
    # ------------------------------------------------------------------

    async def _acquire_session_client(
        self, product_id: Optional[str] = None
    ) -> httpx.AsyncClient:
        """상품 상세 페이지를 방문해 JSESSIONID 쿠키를 획득한 클라이언트를 반환한다.

        잡 시작 시 prepare_abcmart_cache()가 로드한 로그인 쿠키가 있으면
        그 쿠키를 주입한 클라이언트를 즉시 반환(홈/상세 방문 스킵).
        없거나 만료 마킹된 경우 익명 세션 폴백으로 진행한다.

        Args:
          product_id: 세션 획득에 사용할 상품 ID. 제공 시 해당 상품 상세 페이지 방문.

        Returns:
          세션 쿠키가 설정된 httpx.AsyncClient (호출자가 직접 닫아야 함)
        """
        site_label = f"[{self._source_site}]"
        subdomain = self.SUBDOMAIN_MAP.get(self.channel, self.SUBDOMAIN_MAP["10001"])

        # 캐시 미초기화 OR 만료 마킹 시 재로드 — 진입점 어디서든 회복 가능
        # (오토튠/상품관리/주문페이지 enrich는 진입점에서 강제 리셋 후 호출하므로 매 잡 새로 로드)
        # expired=True 마킹 후 확장앱이 새 쿠키 sync한 경우, 다음 호출에서 자동 회복.
        if not self._bulk_cache.get("loaded") or self._bulk_cache.get("expired"):
            try:
                await prepare_abcmart_cache()
            except Exception as e:
                logger.warning(f"{site_label} 쿠키 캐시 lazy-load 실패: {e}")
                ARTSourcingClient._bulk_cache["loaded"] = True  # 재시도 폭주 방지

        # 1. 캐시된 로그인 쿠키 우선 (확장앱이 sync한 사용자 인증 쿠키)
        cache = self._bulk_cache
        cached_cookie = cache.get("cookie") if not cache.get("expired") else None
        if cached_cookie:
            client = httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=True,
                proxy=self._next_proxy(),
            )
            # 쿠키 문자열을 클라이언트에 주입
            try:
                self._inject_cookie_string(client, cached_cookie, subdomain)
                return client
            except Exception as e:
                logger.warning(f"{site_label} 쿠키 주입 실패, 익명 폴백: {e}")
                await client.aclose()

        # 2. 익명 세션 폴백 (기존 동작)
        client = httpx.AsyncClient(
            timeout=self._timeout, follow_redirects=True, proxy=self._next_proxy()
        )
        try:
            # 1단계: 서브도메인 홈 방문으로 초기 쿠키 설정
            resp = await client.get(subdomain + "/", headers=self.HEADERS)
            if resp.status_code in (429, 403):
                retry_after = int(resp.headers.get("Retry-After", "60"))
                logger.warning(f"{site_label} 세션 획득 차단 HTTP {resp.status_code}")
                await client.aclose()
                raise RateLimitError(resp.status_code, retry_after)
            # 2단계: 상품 상세 페이지 방문으로 JSESSIONID 완전히 획득
            # (홈만 방문하면 API 호출 시 빈 응답({})이 반환되는 경우가 있음)
            if product_id:
                detail_page_url = f"{subdomain}/product/new?prdtNo={product_id}"
                if self.channel == self.CHANNEL_GRANDSTAGE:
                    detail_page_url += f"&tChnnlNo={self.channel}"
                detail_resp = await client.get(detail_page_url, headers=self.HEADERS)
                # 홈과 동일하게 레이트리밋 검사 — 차단되면 무효 세션으로 진행하지 않음
                if detail_resp.status_code in (429, 403):
                    retry_after = int(detail_resp.headers.get("Retry-After", "60"))
                    logger.warning(
                        f"{site_label} 상세페이지 세션 획득 차단 HTTP {detail_resp.status_code}"
                    )
                    await client.aclose()
                    raise RateLimitError(detail_resp.status_code, retry_after)
        except RateLimitError:
            raise
        except Exception as e:
            # 세션 획득 실패해도 이후 API 호출 시도는 계속 진행 (JSESSIONID 없이 요청)
            logger.warning(f"{site_label} 세션 획득 실패 (쿠키 없이 진행): {e}")
        return client

    def _inject_cookie_string(
        self, client: httpx.AsyncClient, cookie_str: str, subdomain: str
    ) -> None:
        """확장앱이 보낸 'k1=v1; k2=v2' 형식 쿠키 문자열을 클라이언트에 주입.

        a-rt.com 도메인 모든 서브에 적용 (.a-rt.com).
        """
        if not cookie_str:
            return
        for part in cookie_str.split(";"):
            kv = part.strip()
            if not kv or "=" not in kv:
                continue
            name, _, value = kv.partition("=")
            name = name.strip()
            value = value.strip()
            if name:
                client.cookies.set(name, value, domain=".a-rt.com", path="/")

    async def get_product_info_api(
        self,
        product_id: str,
        client: Optional[httpx.AsyncClient] = None,
    ) -> Optional[dict[str, Any]]:
        """a-rt.com 내부 상품 정보 API 직접 호출.

        SPA가 내부적으로 호출하는 /product/info JSON API를 서버에서 직접 요청한다.
        상품명, 가격, 브랜드, 이미지, 옵션/재고, 카테고리, 제조사 등 핵심 데이터 전부 반환.

        Args:
          product_id: 상품 ID
          client: 세션 쿠키가 이미 설정된 클라이언트. None이면 새 세션을 획득한다.

        Returns:
          info API JSON dict, 실패 시 None
        """
        subdomain = self.SUBDOMAIN_MAP.get(self.channel, self.SUBDOMAIN_MAP["10001"])
        url = f"{subdomain}/product/info?prdtNo={product_id}"
        site_label = f"[{self._source_site}]"
        logger.info(f"{site_label} info API 호출: {product_id}")

        # 외부에서 클라이언트를 주입받은 경우 소유권 없음 (닫지 않음)
        _owns_client = client is None

        try:
            if _owns_client:
                client = await self._acquire_session_client()

            resp = await client.get(url, headers=self.API_HEADERS)

            if resp.status_code in (429, 403):
                retry_after = int(resp.headers.get("Retry-After", "60"))
                logger.warning(f"{site_label} info API 차단 HTTP {resp.status_code}")
                raise RateLimitError(resp.status_code, retry_after)

            if resp.status_code != 200:
                logger.warning(
                    f"{site_label} info API HTTP {resp.status_code}: {product_id}"
                )
                return None

            data = resp.json()
            if data is None:
                logger.warning(
                    f"{site_label} info API null 응답 (상품 없음 or 서버 오류): {product_id}"
                )
                return None
            logger.info(f"{site_label} info API 성공: {product_id}")
            return data

        except RateLimitError:
            raise
        except httpx.TimeoutException:
            logger.error(f"{site_label} info API 타임아웃: {product_id}")
            return None
        except Exception as e:
            logger.error(f"{site_label} info API 실패: {product_id} — {e}")
            return None
        finally:
            if _owns_client and client is not None:
                try:
                    await client.aclose()
                except Exception:
                    pass

    async def get_product_detail_api(
        self,
        product_id: str,
        client: Optional[httpx.AsyncClient] = None,
    ) -> dict[str, str]:
        """a-rt.com 상품 고시정보 API 호출 (/product/info/detail).

        notice 배열에서 소재(재질), 제조국 등 상품정보제공고시 데이터를 수집한다.
        precaution 배열에서 취급주의, authority 배열에서 품질보증 내용을 수집한다.

        Args:
          product_id: 상품 ID
          client: 세션 쿠키가 이미 설정된 클라이언트. None이면 새 세션을 획득한다.

        Returns:
          {"material": "...", "origin_notice": "...", "care_instructions": "...", "quality_guarantee": "...",
           "size": "...", "color_notice": "...", "heel_height": "...", "manufacture_date": "..."} 빈 문자열 가능
        """
        subdomain = self.SUBDOMAIN_MAP.get(self.channel, self.SUBDOMAIN_MAP["10001"])
        url = f"{subdomain}/product/info/detail?prdtNo={product_id}"
        site_label = f"[{self._source_site}]"
        result: dict[str, str] = {
            "material": "",
            "origin_notice": "",
            "care_instructions": "",
            "quality_guarantee": "",
            "size": "",
            "color_notice": "",
            "heel_height": "",
            "manufacture_date": "",
        }

        _owns_client = client is None

        try:
            if _owns_client:
                client = await self._acquire_session_client()

            resp = await client.get(url, headers=self.API_HEADERS)
            if resp.status_code in (429, 403):
                retry_after = int(resp.headers.get("Retry-After", "60"))
                logger.warning(
                    f"{site_label} detail API 차단 HTTP {resp.status_code}: {product_id}"
                )
                raise RateLimitError(resp.status_code, retry_after)
            if resp.status_code != 200:
                return result

            data = resp.json()

            # 고시정보 — 소재, 제조국, 품질보증기준, 치수, 색상, 굽높이, 제조년월
            notice_list = data.get("notice") or []
            for item in notice_list:
                name = (item.get("infoNotcName") or "").strip()
                value = (item.get("prdtAddInfo") or "").strip()
                if name in ("소재", "재질") and value:
                    result["material"] = value
                elif name == "제조국" and value:
                    result["origin_notice"] = value
                elif name == "품질보증기준" and value:
                    result["quality_guarantee"] = value
                elif name in ("치수", "사이즈") and value:
                    result["size"] = value
                elif name == "색상" and value:
                    result["color_notice"] = value
                elif name in ("굽높이", "굽 높이") and value:
                    result["heel_height"] = value
                elif name in ("제조년월", "제조년도") and value:
                    result["manufacture_date"] = value

            # 취급주의 (precaution 배열 첫 번째 항목)
            # [공통][면][패딩] 형태로 오는 경우 [공통] 섹션만 추출
            precaution_list = data.get("precaution") or []
            if precaution_list:
                care = (precaution_list[0].get("prdtAddInfo") or "").strip()
                if care:
                    # [공통] 섹션 추출
                    if "[공통]" in care:
                        import re as _re

                        match = _re.search(r"\[공통\](.*?)(?:\[|$)", care)
                        if match:
                            care = match.group(1).strip()
                    result["care_instructions"] = care

            # 품질보증 (authority 배열 — notice에서 못 찾은 경우 보완)
            if not result["quality_guarantee"]:
                authority_list = data.get("authority") or []
                if authority_list:
                    qa = (authority_list[0].get("prdtAddInfo") or "").strip()
                    if qa:
                        result["quality_guarantee"] = qa

            logger.info(
                f"{site_label} detail API 성공: {product_id} — "
                f"소재={result['material']}, 제조국={result['origin_notice']}"
            )
        except RateLimitError:
            raise
        except Exception as e:
            logger.warning(f"{site_label} detail API 실패: {product_id} — {e}")
        finally:
            if _owns_client and client is not None:
                try:
                    await client.aclose()
                except Exception:
                    pass

        return result

    def _parse_info_api(
        self,
        data: dict[str, Any],
        product_id: str,
        now_iso: str,
        detail_extra: Optional[dict[str, str]] = None,
    ) -> dict[str, Any]:
        """info API JSON 응답을 CollectedProduct 형태로 변환."""
        # 기본 정보
        name = (data.get("prdtName") or "").strip()
        name_en = (data.get("engPrdtName") or "").strip()

        # 브랜드 (유통사명이 brandName으로 내려오는 경우 방어)
        _VENDOR_NAMES = {
            "ABC-MART",
            "ABC마트",
            "ABCmart",
            "GrandStage",
            "그랜드스테이지",
            "GRAND STAGE",
            "debug",  # 비정상 테스트 브랜드명 방어
        }
        brand_info = data.get("brand") or {}
        brand = (brand_info.get("brandName") or "").strip()
        if brand in _VENDOR_NAMES:
            logger.warning(
                f"[{self._source_site}] brandName이 유통사명({brand})으로 내려옴 — 빈값 처리: {product_id}"
            )
            brand = ""
        brand_code = (brand_info.get("brandEnName") or "").strip()

        # 로그인 만료 감지: 캐시된 쿠키로 호출했는데 응답에 loginYn=Y가 없으면 만료
        # → 다음 호출부터 익명 폴백으로 전환
        _login_yn = (data.get("loginYn") or "").upper()
        if self._bulk_cache.get("cookie") and not self._bulk_cache.get("expired"):
            if _login_yn != "Y":
                _mark_abcmart_cookie_expired()

        # 가격: displayProductPrice(페이지 실제 표시가)가 있으면 우선 사용
        # productPrice.sellAmt는 ERP 가격(할인 미반영)이라 페이지가와 다를 수 있음
        # 예: prdtNo=1020115314 → sellAmt=89,000, displayProductPrice=74,000
        price_info = data.get("productPrice") or {}
        original_price = self._safe_int(price_info.get("normalAmt") or 0)
        _sell_amt = self._safe_int(price_info.get("sellAmt") or 0)
        _display_price = self._safe_int(data.get("displayProductPrice") or 0)
        sale_price = _display_price if _display_price > 0 else _sell_amt
        discount_rate = self._safe_int(data.get("displayDiscountRate") or 0)

        if original_price == 0:
            original_price = sale_price

        # 최대혜택가: 멤버십 상시 할인 + 쿠폰 할인
        # API의 alwaysDscntAmt는 풀 가격 기준으로 계산됨
        # 사이트는 쿠폰 후 가격 기준으로 멤버십 할인 적용 → 400원 내외 차이 발생
        # 해결: alwaysDscntAmt로 비율을 역산 후 쿠폰 후 가격에 재적용 (100원 단위 올림)
        _membership_discount = self._safe_int(data.get("alwaysDscntAmt") or 0)
        _always_rate = float(data.get("alwaysDscntRate") or 0)

        # 쿠폰 할인: 로그인 시 받을 수 있는 추가 쿠폰 (없으면 0)
        # maxBenefitCoupon은 중복적용 가능한 쿠폰 묶음(일반+플러스 등) — 전부 합산해야 페이지 표시값과 일치
        _benefit_coupons = data.get("maxBenefitCoupon") or data.get("coupon") or []
        _coupon_discount = sum(
            self._safe_int(c.get("dscntAmt", 0)) for c in _benefit_coupons
        )

        _after_coupon = sale_price - _coupon_discount
        if _membership_discount > 0 and sale_price > 0:
            # alwaysDscntRate가 없으면 alwaysDscntAmt에서 비율 역산 (단위: %)
            if _always_rate <= 0:
                _always_rate = _membership_discount / sale_price * 100
            _membership_on_post_coupon = (
                math.ceil(_after_coupon * _always_rate / 100 / 100) * 100
            )
            best_benefit_price = _after_coupon - _membership_on_post_coupon
        else:
            best_benefit_price = _after_coupon

        if best_benefit_price <= 0 or best_benefit_price > sale_price:
            best_benefit_price = sale_price

        # 이미지 (productImage: 메인 1장, productImageExtra: 추가 이미지)
        images: list[str] = []
        main_images = data.get("productImage") or []
        for img in main_images:
            url = self._normalize_image(img.get("imageUrl") or "")
            if url and url not in images:
                images.append(url)

        extra_images = data.get("productImageExtra") or []
        for img in extra_images:
            url = self._normalize_image(img.get("imageUrl") or "")
            if url and url not in images:
                images.append(url)

        images = images[:9]

        # 옵션/재고 (스타일컬러 복수인 경우 API가 모든 컬러 옵션을 합쳐서 반환 → 동일 사이즈명 중복 가능)
        options: list[dict[str, Any]] = []
        _seen_opt_names: dict[str, int] = {}  # 옵션명 → options 리스트 인덱스
        for opt in data.get("productOption") or []:
            opt_name = (opt.get("optnName") or "").strip()
            if not opt_name:
                continue
            order_qty = self._safe_int(opt.get("orderPsbltQty") or 0)
            sell_stat = opt.get("sellStatCode") or ""
            # sellStatCode "10001"=판매중, 그 외=품절 or orderPsbltQty=0이면 품절
            is_sold_out = sell_stat != "10001" or order_qty == 0
            opt_price_info = opt.get("optionPrice") or {}
            opt_add_amt = self._safe_int(opt_price_info.get("optnAddAmt") or 0)
            entry = {
                "no": int(opt.get("prdtOptnNo") or 0),
                "name": opt_name,
                "price": sale_price + opt_add_amt,
                "stock": order_qty,
                "isSoldOut": is_sold_out,
            }
            if opt_name in _seen_opt_names:
                # 중복 사이즈명 → 재고 있는 쪽 우선 채택
                idx = _seen_opt_names[opt_name]
                if options[idx].get("isSoldOut") and not is_sold_out:
                    options[idx] = entry
            else:
                _seen_opt_names[opt_name] = len(options)
                options.append(entry)

        # 카테고리
        category_str = (data.get("stdCtgrNameInline") or "").strip()
        # "신발 > 스니커즈 > 라이프스타일" 형태
        category_levels = [c.strip() for c in category_str.split(">") if c.strip()]
        if not category_str:
            category_levels = []

        # stdCtgrNameInline 미수집 시 ctgrList / dispCtgrList 폴백
        if not category_levels:
            _CTGR_EXCLUDE = {
                "홈",
                "HOME",
                "ABC마트",
                "그랜드스테이지",
                "GRAND STAGE",
                "ABCmart",
            }
            for field in (
                "ctgrList",
                "dispCtgrList",
                "gnbCtgrList",
                "prdtCtgrList",
                "breadcrumbList",
            ):
                val = data.get(field)
                if isinstance(val, list) and val:
                    parts: list[str] = []
                    for item in val:
                        if isinstance(item, dict):
                            name_val = (
                                item.get("ctgrName")
                                or item.get("name")
                                or item.get("dispCtgrName")
                                or ""
                            ).strip()
                            if name_val and name_val not in _CTGR_EXCLUDE:
                                parts.append(name_val)
                        elif (
                            isinstance(item, str)
                            and item.strip()
                            and item.strip() not in _CTGR_EXCLUDE
                        ):
                            parts.append(item.strip())
                    if parts:
                        category_levels = parts
                        category_str = " > ".join(category_levels)
                        logger.debug(
                            f"[{self._source_site}] 카테고리 폴백({field}) 적용: {category_str}"
                        )
                        break

        # 제조사/원산지
        manufacturer = (data.get("mnftrName") or "").strip()
        # ABCmart 어드민 오입력 방어: URL/금지문자가 mnftrName에 섞여 내려오는 케이스 차단
        # (스마트스토어 manufacturerName 금지문자: \ * ? " < >)
        manufacturer = re.split(r"https?://|www\.", manufacturer, maxsplit=1)[0].strip()
        manufacturer = re.sub(r'[\\*?"<>]', "", manufacturer).strip()
        org_place_code = str(data.get("orgPlaceCode") or "")
        # 고시정보 API의 제조국 텍스트 우선 사용, 없으면 코드 매핑 사용
        origin_notice = (detail_extra or {}).get("origin_notice", "")
        if origin_notice:
            origin = origin_notice
        else:
            origin = self.ORIGIN_CODE_MAP.get(org_place_code, "")
            if origin == "" and org_place_code:
                logger.warning(
                    f"[{self._source_site}] 알 수 없는 orgPlaceCode: {org_place_code} — ORIGIN_CODE_MAP에 추가 필요"
                )

        # 색상
        color = (data.get("prdtColorInfo") or "").strip()

        # 품번 (prdtStyleNo → styleInfo → vndrPrdtNoText 순서로 폴백)
        style_code = (
            data.get("prdtStyleNo")
            or data.get("styleNo")
            or data.get("styleInfo")
            or ""
        ).strip()

        # 나이키 브랜드인 경우 품번-색상 형식으로 조합 (예: CW2288-111)
        if brand.strip() == "나이키" and style_code and color:
            style_code = f"{style_code}-{color}"

        # 성별 (genderGbnCode 코드 매핑 → 상품명 → 카테고리 순 폴백 추출)
        gender_code = str(data.get("genderGbnCode") or "")
        sex = self.GENDER_CODE_MAP.get(gender_code, "")
        if not gender_code:
            logger.debug(
                f"[{self._source_site}] genderGbnCode 없음({product_id}), API 응답 키: {list(data.keys())}"
            )
        if not sex:
            product_name = (data.get("prdtName") or "").lower()
            if any(
                k in product_name
                for k in [
                    "우먼",
                    "women",
                    "woman",
                    "여성",
                    "여아",
                    "girls",
                    "wmns",
                    "w's",
                ]
            ):
                sex = "여성용"
            elif any(
                k in product_name for k in ["맨", "men", "man", "남성", "남아", "boys"]
            ):
                sex = "남성용"
            elif any(
                k in product_name
                for k in [
                    "키즈",
                    "kids",
                    "유아",
                    "아동",
                    "주니어",
                    "junior",
                    " gs",
                    " ps",
                    " td",
                    "infant",
                ]
            ):
                sex = "아동/주니어공용"
            elif any(k in product_name for k in ["유니섹스", "unisex"]):
                sex = "남여공용"
        # 상품명에서도 미수집 시 표준카테고리 → API 응답 전체 필드 순 폴백 (WOMEN이 MEN보다 먼저 체크)
        if not sex:
            cat_upper = category_str.upper()
            if any(k in cat_upper for k in ["WOMEN", "WOMAN", "GIRL", "여성", "여아"]):
                sex = "여성용"
            elif any(k in cat_upper for k in ["MEN", "MAN", "BOY", "남성", "남아"]):
                sex = "남성용"
            elif any(
                k in cat_upper for k in ["KIDS", "JUNIOR", "아동", "주니어", "유아"]
            ):
                sex = "아동/주니어공용"
            elif any(k in cat_upper for k in ["UNISEX", "유니섹스"]):
                sex = "남여공용"
        # 전시카테고리/GNB 카테고리 등 API 응답 내 성별 관련 필드 추가 탐색
        if not sex:
            # info API 응답에 있을 수 있는 카테고리 관련 필드들을 모아 문자열로 합산
            extra_ctgr_parts: list[str] = []
            for field in (
                "dispCtgrList",
                "ctgrList",
                "gnbCtgrList",
                "breadcrumbList",
                "prdtCtgrList",
            ):
                val = data.get(field)
                if isinstance(val, list):
                    for item in val:
                        if isinstance(item, dict):
                            extra_ctgr_parts.extend(str(v) for v in item.values() if v)
                        elif isinstance(item, str):
                            extra_ctgr_parts.append(item)
                elif isinstance(val, str) and val:
                    extra_ctgr_parts.append(val)
            # 문자열 필드도 체크 (gnbCtgrName, dispCtgrName 등)
            for field in ("gnbCtgrName", "dispCtgrName", "prdtCtgrName", "ctgrNavName"):
                val = data.get(field)
                if isinstance(val, str) and val:
                    extra_ctgr_parts.append(val)

            if extra_ctgr_parts:
                extra_upper = " ".join(extra_ctgr_parts).upper()
                if any(
                    k in extra_upper for k in ["WOMEN", "WOMAN", "GIRL", "여성", "여아"]
                ):
                    sex = "여성용"
                elif any(
                    k in extra_upper for k in ["MEN", "MAN", "BOY", "남성", "남아"]
                ):
                    sex = "남성용"
                elif any(
                    k in extra_upper
                    for k in ["KIDS", "JUNIOR", "아동", "주니어", "유아"]
                ):
                    sex = "아동/주니어공용"
                elif any(k in extra_upper for k in ["UNISEX", "유니섹스"]):
                    sex = "남여공용"
        # 모든 폴백 후에도 미결정 시 기본값 '남여공용' 설정
        if not sex:
            sex = "남여공용"
            logger.debug(
                f"[{self._source_site}] 성별 미결정 → 기본값 '남여공용' 적용: {product_id}"
            )

        # 판매 상태
        sell_stat_code = data.get("sellStatCode") or ""
        is_out_of_stock = sell_stat_code != "10001" or (
            bool(options) and all(opt.get("isSoldOut", False) for opt in options)
        )
        sale_status = "sold_out" if is_out_of_stock else "in_stock"

        # 배송 정보 — freeDlvyYn 또는 판매가 >= 무료배송 기준금액이면 무료
        _free_dlvy_stdr = self._safe_int(data.get("freeDlvyStdrAmt", 0))
        free_shipping = data.get("freeDlvyYn") == "Y" or (
            _free_dlvy_stdr > 0 and sale_price >= _free_dlvy_stdr
        )
        same_day_delivery = data.get("dailyDlvyYn") == "Y"
        shipping_fee = 0 if free_shipping else 3000

        # 상세 URL 구성
        subdomain = self.SUBDOMAIN_MAP.get(self.channel, self.SUBDOMAIN_MAP["10001"])
        detail_url = f"{subdomain}/product/new?prdtNo={product_id}"
        if self.channel == self.CHANNEL_GRANDSTAGE:
            detail_url += f"&tChnnlNo={self.channel}"

        return {
            "sourceSite": self._source_site,
            "siteProductId": product_id,
            "sourceUrl": detail_url,
            "name": name,
            "nameEn": name_en,
            "brand": brand,
            "brandCode": brand_code,
            "category": category_str,
            "category1": category_levels[0] if len(category_levels) > 0 else "",
            "category2": category_levels[1] if len(category_levels) > 1 else "",
            "category3": category_levels[2] if len(category_levels) > 2 else "",
            "category4": category_levels[3] if len(category_levels) > 3 else "",
            "images": images,
            "detailImages": [],
            "detailHtml": "",
            "options": options,
            "originalPrice": original_price,
            "salePrice": sale_price,
            "cost": sale_price + shipping_fee,
            "shippingFee": shipping_fee,
            "bestBenefitPrice": best_benefit_price,
            # 호출 시점 사용자 쿠키의 로그인 상태 — refresh()에서 DOM 위임 결정에 사용
            # "Y"면 멤버십 상시할인이 응답에 반영된 정확한 best_benefit_price (DOM 위임 스킵)
            # 그 외(빈값/N)면 비로그인 응답 → 멤버십 누락 → DOM 위임 fallback 필요
            "loginYn": _login_yn,
            "discountRate": discount_rate,
            "saleStatus": sale_status,
            "isOutOfStock": is_out_of_stock,
            "freeShipping": free_shipping,
            "sameDayDelivery": same_day_delivery,
            "manufacturer": manufacturer,
            "origin": origin,
            "color": (detail_extra or {}).get("color_notice", "")
            or color
            or (style_code.split("-")[-1] if "-" in style_code else ""),
            "styleCode": style_code,
            "sex": sex,
            "season": "사계절용",  # API에 시즌 정보 없으므로 기본값
            "material": (detail_extra or {}).get("material", ""),
            "careInstructions": (detail_extra or {}).get("care_instructions", ""),
            "qualityGuarantee": (detail_extra or {}).get("quality_guarantee", ""),
            "sizeNotice": (detail_extra or {}).get("size", ""),
            "colorNotice": (detail_extra or {}).get("color_notice", ""),
            "heelHeight": (detail_extra or {}).get("heel_height", ""),
            "manufactureDate": (detail_extra or {}).get("manufacture_date", ""),
            "collectedAt": now_iso,
            "updatedAt": now_iso,
        }

    # ------------------------------------------------------------------
    # 카테고리 스캔
    # ------------------------------------------------------------------

    async def scan_categories(self, keyword: str) -> dict[str, Any]:
        """키워드 검색 → 전체 페이지 순회 → 카테고리 분포 집계.

        검색 API 응답의 CTGR_NAME_ALL 필드로 상세 조회 없이
        카테고리를 직접 집계한다. (perPage=100, 단일 세션 유지)

        Returns:
          {"categories": [...], "total": int, "groupCount": int}
        """
        site_label = f"[{self._source_site}]"
        logger.info(f"{site_label} 카테고리 스캔 시작: '{keyword}'")

        # 전체 페이지 순회하여 raw 아이템 수집
        all_items, total_count = await self._search_all_for_scan(keyword)
        if not all_items:
            return {"categories": [], "total": 0, "groupCount": 0}

        # 카테고리 집계 (검색 결과에서 직접 추출)
        _CTGR_EXCLUDE = {
            "홈",
            "HOME",
            "ABC마트",
            "그랜드스테이지",
            "GRAND STAGE",
            "ABCmart",
        }
        cat_counter: dict[str, int] = {}

        for item in all_items:
            cat_str = (
                item.get("CTGR_NAME_ALL") or item.get("SY_CTGR_NAME") or ""
            ).strip()
            if not cat_str:
                continue

            # 쉼표 구분 복수 카테고리 → 첫 번째만 사용
            if "," in cat_str:
                cat_str = cat_str.split(",")[0].strip()

            cat_levels = [
                c.strip()
                for c in cat_str.split(">")
                if c.strip() and c.strip() not in _CTGR_EXCLUDE
            ]
            if not cat_levels:
                continue

            c1 = cat_levels[0] if len(cat_levels) > 0 else ""
            c2 = cat_levels[1] if len(cat_levels) > 1 else ""
            c3 = cat_levels[2] if len(cat_levels) > 2 else ""
            path = " > ".join(cat_levels)

            # 카테고리 코드: CTGR_CD_ALL의 마지막 레벨
            code_str = (item.get("CTGR_CD_ALL") or item.get("SY_CTGR_NO") or "").strip()
            if "," in code_str:
                code_str = code_str.split(",")[0].strip()
            code_parts = [c.strip() for c in code_str.split(">") if c.strip()]
            code = code_parts[-1] if code_parts else path

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

        result = {
            "categories": categories,
            "total": sum(c["count"] for c in categories),
            "groupCount": len(categories),
        }
        logger.info(
            f"{site_label} 카테고리 스캔 완료: '{keyword}' "
            f"→ {result['groupCount']}개 카테고리, 총 {result['total']}건"
        )
        return result

    async def _search_all_for_scan(
        self, keyword: str, max_pages: int = 30
    ) -> tuple[list[dict[str, Any]], int]:
        """카테고리 스캔용 전체 페이지 검색. 단일 세션으로 순회.

        Returns:
          (raw_items, total_count)
        """
        site_label = f"[{self._source_site}]"
        subdomain = self.SUBDOMAIN_MAP.get(self.channel, self.SUBDOMAIN_MAP["10001"])
        encoded_kw = quote(keyword)
        search_page_url = (
            f"{subdomain}/display/search-word/result"
            f"?searchWord={encoded_kw}&channel={self.channel}"
        )
        api_url = f"{subdomain}{self.SEARCH_API_PATH}"
        per_page = 100

        all_items: list[dict[str, Any]] = []
        total_count = 0

        # 일시적 연결 오류 대비 최대 2회 시도
        for _attempt in range(2):
            _attempt_items: list[dict[str, Any]] = []
            _attempt_total = 0
            try:
                async with httpx.AsyncClient(
                    timeout=self._timeout,
                    follow_redirects=True,
                    proxy=self._next_proxy(),
                ) as client:
                    # 세션 획득 (JSESSIONID)
                    await client.get(subdomain + "/", headers=self.HEADERS)
                    await client.get(search_page_url, headers=self.HEADERS)

                    api_headers = {
                        **self.API_HEADERS,
                        "Referer": search_page_url,
                    }

                    for page in range(1, max_pages + 1):
                        resp = await client.get(
                            api_url,
                            params={
                                "searchWord": keyword,
                                "page": str(page),
                                "perPage": str(per_page),
                                "sort": "point",
                                "channel": self.channel,
                                "pageColumn": "3",
                                "tabGubun": "total",
                                "searchPageGubun": "product",
                                "smartSearchCheck": "false",
                            },
                            headers=api_headers,
                        )

                        if resp.status_code in (429, 403):
                            logger.warning(
                                f"{site_label} 카테고리 스캔 차단 HTTP {resp.status_code}"
                            )
                            break

                        if resp.status_code != 200:
                            logger.warning(
                                f"{site_label} 카테고리 스캔 HTTP {resp.status_code}"
                            )
                            break

                        data = resp.json()

                        if page == 1:
                            _attempt_total = data.get("SEARCH_COUNT", 0) or 0

                        items = data.get("SEARCH") or []
                        if not items:
                            break

                        _attempt_items.extend(items)
                        logger.info(
                            f"{site_label} 카테고리 스캔 {page}페이지: "
                            f"+{len(items)}건 (누적 {len(_attempt_items)}/{_attempt_total})"
                        )

                        if len(_attempt_items) >= _attempt_total:
                            break

                        # 차단 방지 딜레이
                        await asyncio.sleep(0.3)

            except Exception as e:
                logger.error(
                    f"{site_label} 카테고리 스캔 검색 실패 "
                    f"(시도 {_attempt + 1}/2): {e!r} ({type(e).__name__})"
                )
                if _attempt < 1:
                    await asyncio.sleep(2.0)
                    continue

            if _attempt_items:
                all_items = _attempt_items
                total_count = _attempt_total
                break
            elif _attempt < 1:
                logger.warning(f"{site_label} 카테고리 스캔 결과 없음 — 2초 후 재시도")
                await asyncio.sleep(2.0)

        return all_items, total_count

    # ------------------------------------------------------------------
    # 상세 조회
    # ------------------------------------------------------------------

    async def get_product_detail(
        self,
        product_id: str,
        refresh_only: bool = False,
        shared_client: Optional[httpx.AsyncClient] = None,
    ) -> dict[str, Any]:
        """a-rt.com 상품 상세 정보 조회.

        1순위: 내부 JSON API (/product/info) 직접 호출
        2순위: HTML 페이지 파싱 (API 실패 시 폴백)

        Args:
          product_id: a-rt.com 상품 ID (prdtNo)
          refresh_only: True이면 가격/재고만 빠르게 갱신
          shared_client: 외부에서 주입한 세션 클라이언트 (배치 선취합 시 재사용)
                         None이면 내부에서 새 세션을 획득하고 완료 후 닫는다.

        Returns:
          표준 상품 상세 dict

        Raises:
          RateLimitError: 429/403 응답 시
        """
        site_label = f"[{self._source_site}]"
        now_iso = datetime.now(tz=timezone.utc).isoformat()
        api_failed = False

        # 1순위: 내부 JSON API 직접 호출
        # 세션 쿠키(JSESSIONID)를 한 번 획득한 뒤 info/detail API 양쪽에 재사용한다.
        # (쿠키 없이 호출하면 API가 빈 응답을 반환함)
        # shared_client가 주입되면 세션 획득을 생략하고 기존 쿠키를 재사용한다.
        _owns_session = shared_client is None
        try:
            session_client = (
                shared_client
                if shared_client is not None
                else await self._acquire_session_client(product_id)
            )
            try:
                info_data = await self.get_product_info_api(
                    product_id, client=session_client
                )
                # null 응답 시 1회 재시도 (일시적 서버 오류 대응)
                if info_data is None:
                    await asyncio.sleep(0.5)
                    info_data = await self.get_product_info_api(
                        product_id, client=session_client
                    )
                if info_data and info_data.get("prdtName"):
                    # refresh_only=True 시 가격/재고만 필요 — 고시정보 API 호출 생략
                    detail_extra = (
                        None
                        if refresh_only
                        else await self.get_product_detail_api(
                            product_id, client=session_client
                        )
                    )
                    result = self._parse_info_api(
                        info_data, product_id, now_iso, detail_extra
                    )
                    logger.info(
                        f"{site_label} API 파싱 성공: {product_id} — '{result.get('name')}'"
                    )
                    return result
                logger.warning(
                    f"{site_label} info API 응답 비어있음, HTML 폴백: {product_id}"
                )
                api_failed = True
            finally:
                # shared_client는 호출자가 관리하므로 여기서 닫지 않음
                if _owns_session:
                    await session_client.aclose()
        except RateLimitError:
            raise
        except Exception as e:
            logger.warning(f"{site_label} info API 예외, HTML 폴백: {product_id} — {e}")
            api_failed = True

        # 2순위: HTML 파싱 폴백
        logger.info(f"{site_label} HTML 폴백 상세 조회: {product_id}")
        params = f"prdtNo={product_id}"
        if self.channel:
            params += f"&tChnnlNo={self.channel}"

        url = f"{self.BASE}{self.DETAIL_PATH}?{params}"

        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, follow_redirects=True, proxy=self._next_proxy()
            ) as client:
                resp = await client.get(url, headers=self.HEADERS)

                if resp.status_code in (429, 403):
                    retry_after = int(resp.headers.get("Retry-After", "60"))
                    logger.warning(
                        f"{site_label} 차단 감지 HTTP {resp.status_code}: {product_id}"
                    )
                    raise RateLimitError(resp.status_code, retry_after)

                if resp.status_code != 200:
                    logger.warning(
                        f"{site_label} 상세 페이지 HTTP {resp.status_code}: {product_id}"
                    )
                    return {}

            html = resp.text
            result = self._parse_detail_html(
                html,
                product_id,
                now_iso,
                refresh_only,
                api_failed=api_failed,
            )
            if not result.get("name"):
                logger.warning(
                    f"{site_label} HTML 폴백도 상품명 없음 (삭제/비활성 상품): {product_id}"
                )
                return {"__product_not_found__": True}
            return result

        except RateLimitError:
            raise
        except httpx.TimeoutException:
            logger.error(f"{site_label} 상세 조회 타임아웃: {product_id}")
            return {}
        except Exception as e:
            logger.error(f"{site_label} 상세 조회 실패: {product_id} — {e}")
            return {}

    def _parse_detail_html(
        self,
        html: str,
        product_id: str,
        now_iso: str,
        refresh_only: bool = False,
        api_failed: bool = False,
    ) -> dict[str, Any]:
        """상세 페이지 HTML 파싱.

        우선순위: og 메타태그 → JSON-LD → script 변수 → DOM 패턴
        """
        # 사이트 기본 타이틀 패턴 — 상품이 없는 페이지에서 나타나는 타이틀이므로 상품명으로 사용하지 않음
        # 실제 확인된 패턴: "아트닷컴 - ABC마트 온라인몰", "아트닷컴 - 그랜드스테이지" 등
        _INVALID_TITLE_PATTERNS = (
            "아트닷컴",
            "a-rt.com",
            "ABC마트 온라인몰",
            "그랜드스테이지 온라인몰",
        )

        # 기본 정보 (og 메타태그)
        name = self._extract_meta(html, "og:title") or ""
        # 사이트 기본 타이틀이면 상품명이 아님
        if name and any(pat in name for pat in _INVALID_TITLE_PATTERNS):
            name = ""
        thumbnail = self._normalize_image(self._extract_meta(html, "og:image") or "")

        # JSON-LD에서 추가 정보 추출 (schema.org Product)
        json_ld = self._extract_json_ld(html)
        if json_ld and not name:
            name = json_ld.get("name", "")
        if json_ld and not thumbnail:
            img = json_ld.get("image", "")
            if isinstance(img, list):
                img = img[0] if img else ""
            thumbnail = self._normalize_image(img)

        # 가격 파싱
        sale_price = self._parse_sale_price(html, json_ld)
        original_price = self._parse_original_price(html, json_ld)
        if original_price == 0:
            original_price = sale_price
        best_benefit_price = self._parse_best_benefit_price(html) or sale_price
        if api_failed:
            best_benefit_price = self.API_FAILURE_COST_FALLBACK

        # 브랜드
        brand = self._parse_brand(html, json_ld)

        # 카테고리
        category_levels = self._parse_category(html)
        category_str = " > ".join(category_levels) if category_levels else ""

        # 성별 (카테고리 정보에서 추출 — WOMEN이 MEN보다 먼저 체크)
        sex = ""
        cat_upper = category_str.upper()
        if any(k in cat_upper for k in ["WOMEN", "WOMAN", "GIRL", "여성", "여아"]):
            sex = "여성용"
        elif any(k in cat_upper for k in ["MEN", "MAN", "BOY", "남성", "남아"]):
            sex = "남성용"
        elif any(k in cat_upper for k in ["KIDS", "JUNIOR", "아동", "주니어", "유아"]):
            sex = "아동/주니어공용"
        elif any(k in cat_upper for k in ["UNISEX", "유니섹스"]):
            sex = "남여공용"
        # 카테고리에서도 미결정 시 기본값 '남여공용' 설정
        if not sex:
            sex = "남여공용"

        # 이미지
        images = self._parse_product_images(html, thumbnail)
        detail_images = [] if refresh_only else self._parse_detail_images(html)

        # 옵션
        options = self._parse_options(html)

        # 품절 여부
        is_out_of_stock = self._check_sold_out(html, options)
        sale_status = "sold_out" if is_out_of_stock else "in_stock"

        # 배송 정보
        free_shipping = bool(
            re.search(r"(?:무료배송|무료 배송|배송비\s*무료)", html, re.IGNORECASE)
        )
        # ABC마트 기본 배송비: 무료배송이면 0, 아니면 3000원
        shipping_fee = 0 if free_shipping else 3000

        detail_url = f"{self.BASE}{self.DETAIL_PATH}?prdtNo={product_id}"
        if self.channel:
            detail_url += f"&tChnnlNo={self.channel}"

        return {
            "sourceSite": self._source_site,
            "siteProductId": product_id,
            "sourceUrl": detail_url,
            "name": name.strip(),
            "brand": brand,
            "category": category_str,
            "category1": category_levels[0] if len(category_levels) > 0 else "",
            "category2": category_levels[1] if len(category_levels) > 1 else "",
            "category3": category_levels[2] if len(category_levels) > 2 else "",
            "category4": category_levels[3] if len(category_levels) > 3 else "",
            "images": images[:9],
            "detailImages": detail_images,
            "detailHtml": "",
            "options": options,
            "originalPrice": original_price,
            "salePrice": sale_price,
            "cost": best_benefit_price,
            "shippingFee": shipping_fee,
            "bestBenefitPrice": best_benefit_price,
            "saleStatus": sale_status,
            "isOutOfStock": is_out_of_stock,
            "freeShipping": free_shipping,
            "sameDayDelivery": False,
            "sex": sex,
            "styleCode": "",
            "material": "",
            "color": "",
            "manufacturer": "",
            "origin": "",
            "careInstructions": "",
            "qualityGuarantee": "",
            "collectedAt": now_iso,
            "updatedAt": now_iso,
        }

    # ------------------------------------------------------------------
    # 가격 파싱 헬퍼
    # ------------------------------------------------------------------

    def _parse_sale_price(self, html: str, json_ld: Optional[dict] = None) -> int:
        """판매가 추출."""
        # JSON-LD offers에서 우선 추출
        if json_ld:
            offers = json_ld.get("offers", {})
            if isinstance(offers, list) and offers:
                offers = offers[0]
            if isinstance(offers, dict):
                price = self._safe_int(offers.get("price", 0))
                if price > 0:
                    return price

        # og 메타태그
        price_meta = self._extract_meta(html, "product:price:amount")
        if price_meta:
            price = self._safe_int(re.sub(r"[^\d]", "", price_meta))
            if price > 0:
                return price

        # HTML 패턴
        for pattern in [
            r'class="[^"]*price-cost[^"]*"[^>]*>.*?(\d[\d,]+)',  # a-rt.com 실제 클래스
            r'class="[^"]*sell[_-]?price[^"]*"[^>]*>.*?(\d[\d,]+)',
            r'class="[^"]*sale[_-]?price[^"]*"[^>]*>.*?(\d[\d,]+)',
            r'class="[^"]*price[^"]*"[^>]*>.*?(\d[\d,]+)',
            r"(?:판매가|할인가)[^<]*?(\d[\d,]+)",
        ]:
            price = self._extract_price(html, pattern)
            if price > 0:
                return price

        return 0

    def _parse_original_price(self, html: str, json_ld: Optional[dict] = None) -> int:
        """정상가 추출."""
        for pattern in [
            r'class="[^"]*price-normal-cost[^"]*"[^>]*>.*?(\d[\d,]+)',  # a-rt.com 실제 클래스
            r'class="[^"]*org[_-]?price[^"]*"[^>]*>.*?(\d[\d,]+)',
            r'class="[^"]*original[_-]?price[^"]*"[^>]*>.*?(\d[\d,]+)',
            r'class="[^"]*old[_-]?price[^"]*"[^>]*>.*?(\d[\d,]+)',
            r"(?:정상가|소비자가)[^<]*?(\d[\d,]+)",
        ]:
            price = self._extract_price(html, pattern)
            if price > 0:
                return price
        return 0

    def _parse_best_benefit_price(self, html: str) -> int:
        """최대혜택가 추출."""
        for pattern in [
            r'class="[^"]*benefit[_-]?price[^"]*"[^>]*>.*?(\d[\d,]+)',
            r'class="[^"]*coupon[_-]?price[^"]*"[^>]*>.*?(\d[\d,]+)',
            r"(?:최대혜택가|쿠폰적용가|최대할인가)[^<]*?(\d[\d,]+)",
        ]:
            price = self._extract_price(html, pattern)
            if price > 0:
                return price
        return 0

    # ------------------------------------------------------------------
    # 정보 파싱 헬퍼
    # ------------------------------------------------------------------

    def _parse_brand(self, html: str, json_ld: Optional[dict] = None) -> str:
        """브랜드명 추출."""
        # JSON-LD brand
        if json_ld:
            brand_info = json_ld.get("brand", {})
            if isinstance(brand_info, dict):
                brand = brand_info.get("name", "")
            else:
                brand = str(brand_info)
            if brand:
                return brand.strip()

        for pattern in [
            r'class="[^"]*prod-brand[^"]*"[^>]*>([^<]+)',  # a-rt.com 실제 클래스
            r'class="[^"]*brand[_-]?name[^"]*"[^>]*>([^<]+)',
            r'class="[^"]*brand[^"]*"[^>]*>\s*<(?:a|span)[^>]*>([^<]+)',
            r'class="[^"]*maker[^"]*"[^>]*>([^<]+)',
        ]:
            brand = self._extract_text(html, pattern)
            if brand:
                return brand.strip()
        return ""

    async def _fetch_breadcrumb_sex(self, product_id: str) -> str:
        """상품 상세 HTML 페이지에서 브레드크럼(li.crumb) 텍스트로 성별 추출.

        a-rt.com 브레드크럼은 <li class="crumb"> 안에 <a> 없이 텍스트 노드로
        MEN/WOMEN/KIDS 등 성별 카테고리가 직접 존재한다.
        """
        params = f"prdtNo={product_id}"
        if self.channel:
            params += f"&tChnnlNo={self.channel}"
        url = f"{self.BASE}{self.DETAIL_PATH}?{params}"
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, follow_redirects=True, proxy=self._next_proxy()
            ) as client:
                resp = await client.get(url, headers=self.HEADERS)
                if resp.status_code != 200:
                    return ""
            html = resp.text
            return self._parse_sex_from_breadcrumb(html)
        except Exception as e:
            logger.debug(
                f"[{self._source_site}] 브레드크럼 성별 조회 실패: {product_id} — {e}"
            )
            return ""

    def _parse_sex_from_breadcrumb(self, html: str) -> str:
        """HTML에서 li.crumb 텍스트 노드로 성별 감지.

        구조: <li class="crumb">MEN</li> (a 태그 없이 텍스트 직접 포함)
        WOMEN이 MEN보다 먼저 체크되어야 함.
        """
        # id="prdtCtgrCrumb" 또는 class="breadcrumb-wrap" 영역에서 li.crumb 텍스트 추출
        crumb_section = re.search(
            r'(?:id="prdtCtgrCrumb"|class="breadcrumb-wrap[^"]*")[^>]*>.*?</(?:ol|ul|div)',
            html,
            re.DOTALL | re.IGNORECASE,
        )
        if not crumb_section:
            crumb_section = re.search(
                r'class="breadcrumb-list[^"]*"[^>]*>(.*?)</(?:ol|ul)',
                html,
                re.DOTALL | re.IGNORECASE,
            )

        source = crumb_section.group(0) if crumb_section else html

        # li 태그 내 텍스트 추출 (a 태그 포함/미포함 모두)
        li_texts = re.findall(
            r'<li[^>]*class="[^"]*crumb[^"]*"[^>]*>(.*?)</li>',
            source,
            re.DOTALL | re.IGNORECASE,
        )
        for raw in li_texts:
            # 태그 제거 후 순수 텍스트 추출
            text = re.sub(r"<[^>]+>", "", raw).strip().upper()
            if not text:
                continue
            if "WOMEN" in text or "여성" in text:
                return "여성용"
            if "MEN" in text or "남성" in text:
                return "남성용"
            if "KIDS" in text or "키즈" in text or "아동" in text or "주니어" in text:
                return "아동/주니어공용"
            if "UNISEX" in text or "남녀공용" in text or "유니섹스" in text:
                return "남여공용"
        return ""

    def _parse_category(self, html: str) -> list[str]:
        """카테고리 경로 추출."""
        EXCLUDE = {"홈", "HOME", "ABC마트", "그랜드스테이지"}
        categories: list[str] = []

        # prdtCtgrCrumb / breadcrumb-wrap 영역 추출
        breadcrumb_pattern = re.compile(
            r'(?:id="prdtCtgrCrumb"|class="breadcrumb[^"]*")[^>]*>(.*?)</(?:ul|ol|div|nav)',
            re.DOTALL | re.IGNORECASE,
        )
        bc = breadcrumb_pattern.search(html)
        if bc:
            section = bc.group(1)

            # 1순위: <a> 태그 텍스트 (구버전 구조)
            link_texts = re.findall(r"<a[^>]*>([^<]+)</a>", section)
            for text in link_texts:
                text = text.strip()
                if text and text not in EXCLUDE:
                    categories.append(text)

            # 2순위: <option selected> 텍스트 (현재 구조 — select 드롭다운)
            if not categories:
                # 각 <li class="crumb"> 안의 selected option 텍스트를 순서대로 추출
                li_blocks = re.findall(
                    r'<li[^>]*class="[^"]*crumb[^"]*"[^>]*>(.*?)</li>',
                    section,
                    re.DOTALL | re.IGNORECASE,
                )
                for block in li_blocks:
                    selected = re.search(
                        r"<option\s+selected[^>]*>([^<]+)</option>",
                        block,
                        re.IGNORECASE,
                    )
                    if selected:
                        text = selected.group(1).strip()
                        if text and text not in EXCLUDE:
                            categories.append(text)

        # og:category 메타태그 폴백
        if not categories:
            cat_meta = self._extract_meta(html, "product:category")
            if cat_meta:
                categories = [c.strip() for c in cat_meta.split(">") if c.strip()]

        return categories[:4]

    def _parse_product_images(self, html: str, thumbnail: str) -> list[str]:
        """상품 이미지 목록 추출 (최대 9장)."""
        images: list[str] = []
        if thumbnail:
            images.append(thumbnail)

        # a-rt.com 이미지 패턴 (CDN: img.a-rt.com 또는 직접 URL)
        img_pattern = re.compile(
            r'(?:src|data-src|data-lazy)=["\']([^"\']*(?:a-rt\.com|abcmart)[^"\']+\.(jpg|jpeg|png|webp))["\']',
            re.IGNORECASE,
        )
        for m in img_pattern.finditer(html):
            img_url = self._normalize_image(m.group(1))
            if img_url and img_url not in images:
                images.append(img_url)
                if len(images) >= 9:
                    break

        # 이미지가 부족하면 og:image 계열 일반 이미지도 수집
        if len(images) < 3:
            general_pattern = re.compile(
                r'class="[^"]*(?:thumb|gallery|slide|img)[^"]*"[^>]*>.*?<img[^>]+src="([^"]+)"',
                re.DOTALL | re.IGNORECASE,
            )
            for m in general_pattern.finditer(html):
                img_url = self._normalize_image(m.group(1))
                if img_url and img_url not in images:
                    images.append(img_url)
                    if len(images) >= 9:
                        break

        return images[:9]

    def _parse_detail_images(self, html: str) -> list[str]:
        """상세 설명 영역에서 이미지 추출.

        ABC마트 상세영역은 lazy-load라 실제 URL이 data-src/data-lazy에 있음.
        1차: 상세영역 컨테이너를 특정하여 내부 이미지 모두 추출
        2차: 컨테이너 매칭 실패 시 a-rt.com/abcmart CDN 이미지를 전체에서 수집
        """
        images: list[str] = []

        # 상세영역 컨테이너 탐색 — id/class에 detail 포함
        detail_area = re.search(
            r'(?:id="[^"]*detail[^"]*"|class="[^"]*detail[_-]?(?:cont|desc|info|area|section)[^"]*")[^>]*>(.*)',
            html,
            re.DOTALL | re.IGNORECASE,
        )
        # src + data-src + data-lazy 전부 캡처 (lazy-load 대응)
        img_pattern = re.compile(
            r'<img[^>]+(?:src|data-src|data-lazy|data-original)=["\']([^"\']+)["\']',
            re.IGNORECASE,
        )

        if detail_area:
            for m in img_pattern.finditer(detail_area.group(1)):
                img_url = self._normalize_image(m.group(1))
                if img_url and img_url not in images:
                    images.append(img_url)

        # 컨테이너 매칭 실패/부족 시 CDN 패턴 기반 전체 스캔 fallback
        if len(images) < 2:
            cdn_pattern = re.compile(
                r'(?:src|data-src|data-lazy|data-original)=["\']'
                r'([^"\']*(?:a-rt\.com|abcmart)[^"\']+\.(?:jpg|jpeg|png|webp|gif))["\']',
                re.IGNORECASE,
            )
            for m in cdn_pattern.finditer(html):
                img_url = self._normalize_image(m.group(1))
                if img_url and img_url not in images:
                    images.append(img_url)

        return images

    @staticmethod
    def _dedup_options(options: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """동일 옵션명 중복 제거 — 재고 있는 쪽 우선 채택."""
        seen: dict[str, int] = {}
        result: list[dict[str, Any]] = []
        for opt in options:
            name = opt.get("name", "")
            if name in seen:
                # 기존 항목이 품절이고 새 항목에 재고 있으면 교체
                if result[seen[name]].get("isSoldOut") and not opt.get("isSoldOut"):
                    result[seen[name]] = opt
            else:
                seen[name] = len(result)
                result.append(opt)
        return result

    def _parse_options(self, html: str) -> list[dict[str, Any]]:
        """옵션(사이즈/색상) 정보 추출."""
        options: list[dict[str, Any]] = []

        # 방법 1: script 변수에서 옵션 JSON 추출
        option_json_pattern = re.compile(
            r"(?:optionData|optionList|itemOptList|goodsOptionList)\s*[=:]\s*(\[.*?\])\s*[;,]",
            re.DOTALL,
        )
        json_match = option_json_pattern.search(html)
        if json_match:
            try:
                option_list = json.loads(json_match.group(1))
                for opt in option_list:
                    opt_name = (
                        opt.get("optNm") or opt.get("optionNm") or opt.get("name") or ""
                    ).strip()
                    if not opt_name:
                        continue
                    opt_price = self._safe_int(
                        opt.get("addPrc")
                        or opt.get("addPrice")
                        or opt.get("price")
                        or 0
                    )
                    opt_stock = self._safe_int(
                        opt.get("stockQty") or opt.get("stock") or 0
                    )
                    is_sold_out = (
                        opt.get("soldOutYn", "N") == "Y"
                        or opt.get("isSoldOut", False)
                        or opt_stock == 0
                    )
                    options.append(
                        {
                            "name": opt_name,
                            "price": opt_price,
                            "stock": opt_stock,
                            "isSoldOut": bool(is_sold_out),
                        }
                    )
                if options:
                    return self._dedup_options(options)
            except (json.JSONDecodeError, TypeError):
                pass

        # 방법 2: 셀렉트박스에서 옵션 추출
        option_area = re.search(
            r'class="[^"]*option[^"]*"[^>]*>(.*?)</select>',
            html,
            re.DOTALL | re.IGNORECASE,
        )
        if option_area:
            opt_pattern = re.compile(
                r'<option[^>]+value="([^"]*)"[^>]*>([^<]+)</option>',
                re.IGNORECASE,
            )
            for value, text in opt_pattern.findall(option_area.group(1)):
                text = text.strip()
                if not value or "선택" in text:
                    continue
                is_sold_out = "품절" in text
                price_match = re.search(r"\(([+-]?\d[\d,]*)\)", text)
                extra_price = 0
                if price_match:
                    extra_price = self._safe_int(
                        re.sub(r"[^\d\-]", "", price_match.group(1))
                    )
                options.append(
                    {
                        "name": text,
                        "price": extra_price,
                        "stock": 0 if is_sold_out else 1,
                        "isSoldOut": is_sold_out,
                    }
                )

        # 방법 3: 사이즈 버튼 영역에서 추출
        if not options:
            size_area = re.search(
                r'class="[^"]*size[_-]?(?:select|list|wrap)[^"]*"[^>]*>(.*?)</(?:ul|div)',
                html,
                re.DOTALL | re.IGNORECASE,
            )
            if size_area:
                btn_pattern = re.compile(
                    r"<(?:button|a|li)[^>]*>([^<]+)</(?:button|a|li)>",
                    re.IGNORECASE,
                )
                for btn_text in btn_pattern.findall(size_area.group(1)):
                    btn_text = btn_text.strip()
                    if not btn_text or len(btn_text) > 30:
                        continue
                    is_sold_out = "품절" in btn_text
                    options.append(
                        {
                            "name": btn_text,
                            "price": 0,
                            "stock": 0 if is_sold_out else 1,
                            "isSoldOut": is_sold_out,
                        }
                    )

        return self._dedup_options(options)

    def _check_sold_out(self, html: str, options: list[dict[str, Any]]) -> bool:
        """품절 여부 판단."""
        # HTML 품절 마커
        if re.search(r'class="[^"]*sold[_-]?out[^"]*"', html, re.IGNORECASE):
            return True

        # 구매 버튼 영역의 품절 텍스트
        btn_area = re.search(
            r'class="[^"]*(?:buy|cart|purchase)[_-]?(?:btn|button|area)[^"]*"[^>]*>(.*?)</div>',
            html,
            re.DOTALL | re.IGNORECASE,
        )
        if btn_area and re.search(
            r"(?:품절|일시품절|SOLD\s*OUT)",
            btn_area.group(1),
            re.IGNORECASE,
        ):
            return True

        # 모든 옵션이 품절
        if options and all(opt.get("isSoldOut", False) for opt in options):
            return True

        return False

    # ------------------------------------------------------------------
    # JSON-LD 추출
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_json_ld(html: str) -> Optional[dict]:
        """JSON-LD (schema.org Product) 추출."""
        json_ld_pattern = re.compile(
            r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
            re.DOTALL | re.IGNORECASE,
        )
        for m in json_ld_pattern.finditer(html):
            try:
                data = json.loads(m.group(1).strip())
                if isinstance(data, list):
                    data = next(
                        (d for d in data if d.get("@type") == "Product"),
                        data[0] if data else {},
                    )
                if isinstance(data, dict) and data.get("@type") == "Product":
                    return data
            except (json.JSONDecodeError, TypeError):
                continue
        return None

    # ------------------------------------------------------------------
    # 공통 헬퍼
    # ------------------------------------------------------------------

    def _normalize_image(self, url: str) -> str:
        """이미지 URL 정규화 (프로토콜 보정)."""
        if not url:
            return ""
        url = url.strip()
        if url.startswith("//"):
            return f"https:{url}"
        if not url.startswith("http"):
            return ""
        return url

    @staticmethod
    def _extract_meta(html: str, prop: str) -> Optional[str]:
        """og/product 메타 태그에서 content 추출."""
        pattern = (
            rf'<meta[^>]+(?:property|name)="{re.escape(prop)}"[^>]+content="([^"]*)"'
        )
        m = re.search(pattern, html, re.IGNORECASE)
        if m:
            return m.group(1)
        # content가 먼저 오는 경우
        pattern2 = (
            rf'<meta[^>]+content="([^"]*)"[^>]+(?:property|name)="{re.escape(prop)}"'
        )
        m2 = re.search(pattern2, html, re.IGNORECASE)
        return m2.group(1) if m2 else None

    @staticmethod
    def _extract_text(html: str, pattern: str) -> str:
        """정규식으로 텍스트 추출."""
        m = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
        return m.group(1).strip() if m else ""

    @staticmethod
    def _extract_price(html: str, pattern: str) -> int:
        """정규식으로 가격 추출."""
        m = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
        if m:
            digits = re.sub(r"[^\d]", "", m.group(1))
            return int(digits) if digits else 0
        return 0

    @staticmethod
    def _safe_int(value: Any) -> int:
        """안전한 정수 변환."""
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            digits = re.sub(r"[^\d]", "", value)
            return int(digits) if digits else 0
        return 0
