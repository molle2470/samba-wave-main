"""GS샵 소싱용 웹 스크래핑 클라이언트 - httpx 기반.

주의: proxy/gsshop.py는 판매처(마켓) 등록용 제휴 API V3 클라이언트이므로,
소싱(상품 수집)용은 이 파일에서 별도로 관리한다.

GS샵 사이트 정보:
  - PC 상세: https://www.gsshop.com/prd/prd.gs?prdid={상품번호}
  - 모바일 상세: https://m.gsshop.com/prd/prd.gs?prdid={상품번호}
  - 이미지 CDN: asset.m-gs.kr, static.m-gs.kr
  - 데이터 소스: 모바일 상세 페이지의 `var renderJson = {...}` 인라인 JSON
  - 검색: GS샵은 검색 URL을 서버단에서 차단(405) → 확장앱 큐(SourcingQueue) 위임

파싱 전략 우선순위:
  1. renderJson (모바일 상세) - 가격, 옵션, 이미지, 카테고리, 배송 전부 포함
  2. JSON-LD (schema.org Product) - 이름, 가격, 브랜드, 이미지 (폴백)
  3. og 메타 태그 - 최소 폴백
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from typing import Any, Optional
import httpx

from backend.utils.logger import logger


class RateLimitError(Exception):
    """GS샵 차단 감지 (429/403)."""

    def __init__(self, status: int, retry_after: int = 0):
        self.status = status
        self.retry_after = retry_after
        super().__init__(f"HTTP {status} (retry_after={retry_after})")


class ProductNotFoundError(Exception):
    """GS샵 상품 영구 삭제(404) 감지."""

    def __init__(self, product_id: str):
        self.product_id = product_id
        super().__init__(f"GS샵 상품 데이터 없음: {product_id}")


class GsShopSourcingClient:
    """GS샵 소싱용 웹 스크래핑 클라이언트 (검색, 상세).

    모바일 상세 페이지(m.gsshop.com)의 renderJson 변수에서
    상품 정보를 추출한다. TV홈쇼핑 기반이므로 보수적 간격으로 요청한다.
    """

    # 카테고리 스캔 진행 상황 (프론트 폴링용)
    scan_progress: dict[str, Any] = {}

    # sourcing_queue.py의 SITE_SEARCH_URLS["GSShop"]이 잘못된 URL이므로 여기서 올바른 URL 사용
    SEARCH_URL = "https://www.gsshop.com/shop/search/main.gs?tq={keyword}"
    BASE_PC = "https://www.gsshop.com"
    BASE_MOBILE = "https://m.gsshop.com"
    PRODUCT_URL = "https://m.gsshop.com/prd/prd.gs"
    MAIN_URL = "https://www.gsshop.com/index.gs"

    HEADERS_MOBILE: dict[str, str] = {
        "User-Agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) "
            "Version/16.0 Mobile/15E148 Safari/604.1"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Referer": "https://m.gsshop.com/",
    }

    HEADERS_PC: dict[str, str] = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Referer": "https://www.gsshop.com/",
    }

    def __init__(
        self, cookie: str = "", *, proxy_pool: list[str] | None = None
    ) -> None:
        self._timeout = httpx.Timeout(30.0, connect=10.0)
        self.cookie = cookie
        self._proxy_pool = proxy_pool or []
        self._proxy_idx = 0

    def _next_proxy(self) -> str | None:
        """프록시 풀에서 다음 프록시 반환 (라운드로빈)."""
        if not self._proxy_pool:
            return None
        proxy = self._proxy_pool[self._proxy_idx % len(self._proxy_pool)]
        self._proxy_idx += 1
        return proxy

    def _headers(
        self,
        mobile: bool = True,
        extra: Optional[dict[str, str]] = None,
    ) -> dict[str, str]:
        """요청 헤더 생성."""
        h = {**(self.HEADERS_MOBILE if mobile else self.HEADERS_PC)}
        if self.cookie:
            h["Cookie"] = self.cookie
        if extra:
            h.update(extra)
        return h

    # ------------------------------------------------------------------
    # 검색
    # ------------------------------------------------------------------

    async def search(
        self,
        keyword: str,
        max_count: int = 40,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """worker 호환 검색 — search_products 래핑."""
        url = kwargs.get("url", "")
        products = await self.search_products(keyword, size=max_count, url=url)
        return {"products": products, "total": len(products)}

    async def search_products(
        self,
        keyword: str,
        page: int = 1,
        size: int = 9999,
        url: str = "",
        **filters: Any,
    ) -> list[dict[str, Any]]:
        """GS샵 상품 검색 — 서버단 직접 크롤링 방식.

        PC 검색 페이지(백화점 탭)를 직접 순회하며 상품 ID를 수집한다.
        scan_categories()와 동일한 서버단 접근 방식 사용.

        Args:
          keyword: 검색 키워드
          page: 페이지 번호 (현재 미사용)
          size: 최대 결과 수
          url: 그룹 link URL (미사용, 호환성 유지)

        Returns:
          표준 상품 dict 리스트 (site_product_id 포함)
        """
        import base64

        logger.info(f'[GSSHOP] 검색 시작 (서버 직접): "{keyword}" max={size}')

        link_pattern = re.compile(
            r"/(?:prd/prd\.gs\?prdid|deal/deal\.gs\?dealNo)=(\d+)"
        )
        product_ids: list[str] = []
        seen_ids: set[str] = set()
        now_iso = datetime.now(tz=timezone.utc).isoformat()

        try:
            _proxy = self._next_proxy()
            _ck: dict[str, Any] = {
                "timeout": self._timeout,
                "follow_redirects": True,
            }
            if _proxy:
                _ck["proxy"] = _proxy

            async with httpx.AsyncClient(**_ck) as client:
                for pg in range(1, 300):
                    # 백화점 탭 eh 파라미터 생성 (scan_categories 동일)
                    if pg == 1:
                        eh = base64.b64encode(
                            json.dumps(
                                {"part": "DEPT", "selected": "opt-part"},
                                separators=(",", ":"),
                            ).encode()
                        ).decode()
                    else:
                        eh = base64.b64encode(
                            json.dumps(
                                {
                                    "pageNumber": pg,
                                    "part": "DEPT",
                                    "selected": "opt-page",
                                },
                                separators=(",", ":"),
                            ).encode()
                        ).decode()

                    try:
                        resp = await client.get(
                            f"{self.BASE_PC}/shop/search/main.gs",
                            params={"tq": keyword, "eh": eh},
                            headers=self._headers(mobile=False),
                        )
                    except Exception as e:
                        logger.warning(f"[GSSHOP] 검색 페이지 요청 실패 pg={pg}: {e}")
                        break

                    if resp.status_code != 200:
                        logger.warning(
                            f"[GSSHOP] 검색 페이지 HTTP {resp.status_code}: pg={pg}"
                        )
                        break

                    # prd-list 섹션만 추출 (사이드바/배너 상품 제외)
                    _prd_section = re.search(
                        r'<section[^>]+class="prd-list"[^>]*>(.*?)</section>',
                        resp.text,
                        re.DOTALL,
                    )
                    _search_html = _prd_section.group(1) if _prd_section else resp.text

                    new_count = 0
                    for pid in link_pattern.findall(_search_html):
                        if pid not in seen_ids:
                            seen_ids.add(pid)
                            product_ids.append(pid)
                            new_count += 1

                    if new_count == 0:
                        break

                    if len(product_ids) >= size:
                        break

                    # 페이지 간 딜레이 (차단 방지)
                    await asyncio.sleep(0.2)

            products = [
                {
                    "site_product_id": pid,
                    "name": "(GSShop)",
                    "sale_price": 1,
                    "original_price": 0,
                    "source_site": "GSSHOP",
                    "source_url": f"{self.BASE_PC}/prd/prd.gs?prdid={pid}",
                    "collectedAt": now_iso,
                    "status": "collected",
                }
                for pid in product_ids[:size]
            ]
            logger.info(
                f'[GSSHOP] 검색 완료: "{keyword}" → {len(products)}개 ({pg}페이지 순회)'
            )
            return products

        except Exception as e:
            logger.error(f"[GSSHOP] 검색 실패: {keyword} — {e}")
            return []

    def _parse_main_products(
        self, html: str, keyword: str, size: int
    ) -> list[dict[str, Any]]:
        """PC 메인 페이지 entryData에서 상품 목록 추출."""
        products: list[dict[str, Any]] = []
        seen: set[str] = set()
        kw_lower = keyword.lower()

        # entryData JSON 추출
        entry_match = re.search(
            r'<script[^>]+id="entryData"[^>]*>\s*(.*?)\s*</script>',
            html,
            re.DOTALL,
        )
        if not entry_match:
            return products

        try:
            entry_data = json.loads(entry_match.group(1))
        except (json.JSONDecodeError, TypeError):
            return products

        # mainBigBannerList + 기타 상품 리스트에서 추출
        all_items: list[dict[str, Any]] = []
        for key in entry_data:
            val = entry_data[key]
            if isinstance(val, list):
                all_items.extend(
                    item
                    for item in val
                    if isinstance(item, dict) and item.get("dealNo")
                )

        now_iso = datetime.now(tz=timezone.utc).isoformat()

        for item in all_items:
            deal_no = str(item.get("dealNo", ""))
            if not deal_no or deal_no in seen:
                continue

            name = item.get("exposPrdNm", "").strip()
            brand = item.get("dealEtc2Nm", "").strip()

            # 키워드 필터링
            search_text = f"{name} {brand}".lower()
            if kw_lower and kw_lower not in search_text:
                continue

            seen.add(deal_no)
            gs_prc = self._safe_int(item.get("gsPrc", 0))
            sale_prc = self._safe_int(item.get("salePrc", 0))

            products.append(
                {
                    "siteProductId": deal_no,
                    "goodsNo": deal_no,
                    "sourceSite": "GSSHOP",
                    "sourceUrl": f"{self.BASE_PC}/prd/prd.gs?prdid={deal_no}",
                    "name": name,
                    "brand": brand,
                    "salePrice": gs_prc or sale_prc,
                    "originalPrice": sale_prc or gs_prc,
                    "discountRate": self._safe_int(item.get("dcRt", 0)),
                    "image": item.get("bigBannerUrl", "") or item.get("imgUrl", ""),
                    "isSoldOut": bool(item.get("isTempout")),
                    "freeShipping": item.get("freeDlvYn") == "Y",
                    "collectedAt": now_iso,
                    "status": "collected",
                }
            )

            if len(products) >= size:
                break

        return products

    # ------------------------------------------------------------------
    # 카테고리 스캔 — 검색 → 상세 조회 → 카테고리 집계 (무신사 패턴)
    # ------------------------------------------------------------------

    # GNB 대카테고리 매핑 (lsectNm → GNB 상위 카테고리)
    GNB_MAP: dict[str, str] = {
        "스포츠의류": "스포츠/레저",
        "등산/아웃도어": "스포츠/레저",
        "스포츠신발": "스포츠/레저",
        "스포츠가방": "스포츠/레저",
        "스포츠잡화": "스포츠/레저",
        "골프의류": "스포츠/레저",
        "골프용품": "스포츠/레저",
        "골프클럽": "스포츠/레저",
        "수영/물놀이": "스포츠/레저",
        "캠핑용품": "스포츠/레저",
        "헬스/요가": "스포츠/레저",
        "자전거": "스포츠/레저",
        "낚시용품": "스포츠/레저",
        "구기/라켓": "스포츠/레저",
        "스키/스노보드": "스포츠/레저",
        "스케이트/보드": "스포츠/레저",
        "시즌의류/잡화": "스포츠/레저",
        "주니어/키즈의류": "출산/유아동",
        "유아동잡화": "출산/유아동",
        "신생아/유아의류": "출산/유아동",
        "티셔츠": "유니섹스의류",
        "아우터": "유니섹스의류",
        "바지": "유니섹스의류",
        "맨투맨/후드집업": "유니섹스의류",
        "셔츠/남방": "유니섹스의류",
        "니트/가디건": "유니섹스의류",
        "조끼": "유니섹스의류",
        "수트/셋업": "유니섹스의류",
        "원피스": "여성의류",
        "스커트": "여성의류",
        "블라우스/셔츠": "여성의류",
        "가방/지갑": "패션잡화",
        "신발": "패션잡화",
        "여행가방/소품": "패션잡화",
        "양말/패션소품": "패션잡화",
        "주얼리/시계": "패션잡화",
        "휴대폰/태블릿": "가전/디지털",
        "음향기기": "가전/디지털",
        "자동차기기": "가전/디지털",
        "주방용품": "생활/주방",
        "현대백화점": "백화점",
        "롯데백화점": "백화점",
    }

    async def scan_categories(
        self,
        keyword: str,
    ) -> dict[str, Any]:
        """GS샵 카테고리 스캔 — 백화점 탭 전체 페이지 검색 → 상세 조회 → 카테고리 집계.

        1. 백화점 탭 URL로 전체 페이지 순회 (서버 직접) → 상품 ID 목록
        2. 전체 상품 상세 조회 (서버 직접, 동시 100) → renderJson 카테고리 추출
        3. GNB 대카테고리 매핑 + 카테고리별 상품 수 집계

        Returns:
          {"categories": [...], "total": int, "groupCount": int}
        """
        import base64

        logger.info(f'[GSSHOP] 카테고리 스캔 시작: "{keyword}"')
        GsShopSourcingClient.scan_progress = {
            "stage": "search",
            "keyword": keyword,
            "page": 0,
            "products": 0,
            "detail_ok": 0,
            "detail_fail": 0,
            "detail_total": 0,
        }

        # 1. 백화점 탭 전체 페이지 순회 → 상품 ID 수집 (서버 직접)
        eh_dept = base64.b64encode(
            json.dumps(
                {"part": "DEPT", "selected": "opt-part"}, separators=(",", ":")
            ).encode()
        ).decode()
        product_ids: list[str] = []
        seen_ids: set[str] = set()
        link_pattern = re.compile(
            r"/(?:prd/prd\.gs\?prdid|deal/deal\.gs\?dealNo)=(\d+)"
        )

        async with httpx.AsyncClient(
            timeout=self._timeout, follow_redirects=True
        ) as client:
            for page in range(1, 100):
                if page == 1:
                    params = {"tq": keyword, "eh": eh_dept}
                else:
                    eh_page = base64.b64encode(
                        json.dumps(
                            {
                                "pageNumber": page,
                                "part": "DEPT",
                                "selected": "opt-page",
                            },
                            separators=(",", ":"),
                        ).encode()
                    ).decode()
                    params = {"tq": keyword, "eh": eh_page}
                try:
                    resp = await client.get(
                        f"{self.BASE_PC}/shop/search/main.gs",
                        params=params,
                        headers=self._headers(mobile=False),
                    )
                    # prd-list 섹션만 추출 (사이드바/배너 상품 제외)
                    _prd_sec = re.search(
                        r'<section[^>]+class="prd-list"[^>]*>(.*?)</section>',
                        resp.text,
                        re.DOTALL,
                    )
                    _scan_html = _prd_sec.group(1) if _prd_sec else resp.text
                    new_count = 0
                    for pid in link_pattern.findall(_scan_html):
                        if pid not in seen_ids:
                            seen_ids.add(pid)
                            product_ids.append(pid)
                            new_count += 1
                    if new_count == 0:
                        break
                    GsShopSourcingClient.scan_progress.update(
                        {"page": page, "products": len(product_ids)}
                    )
                except Exception:
                    break

        if not product_ids:
            logger.warning(f'[GSSHOP] 카테고리 스캔: 검색 결과 없음 "{keyword}"')
            GsShopSourcingClient.scan_progress = {}
            return {"categories": [], "total": 0, "groupCount": 0}

        logger.info(
            f"[GSSHOP] 카테고리 스캔: {len(product_ids)}개 상품 검색 완료, 상세 조회 시작"
        )

        # 2. 전체 상품 상세 조회 → 카테고리 추출
        #    500개씩 청크, 프록시 3개 순차 로테이션, 청크당 동시 20
        CHUNK_SIZE = 500
        sem = asyncio.Semaphore(20)
        scan_timeout = httpx.Timeout(30.0, connect=15.0)
        cat_counter: dict[str, int] = {}
        ok_count = 0
        fail_count = 0
        GsShopSourcingClient.scan_progress.update(
            {"stage": "detail", "detail_total": len(product_ids)}
        )

        async def _fetch_detail(
            client: httpx.AsyncClient, pid: str
        ) -> Optional[dict[str, Any]]:
            """스캔 전용 상세 조회 (프록시별 클라이언트 사용)."""
            url = f"{self.PRODUCT_URL}?prdid={pid}"
            try:
                resp = await client.get(url, headers=self._headers(mobile=True))
                if resp.status_code != 200:
                    logger.warning(
                        f"[GSSHOP] 카테고리 스캔 상세 실패: {pid} — HTTP {resp.status_code}"
                    )
                    return None
                render_data = self._extract_render_json(resp.text)
                if render_data:
                    return self._build_from_render_json(render_data, pid, 0, "")
                logger.warning(
                    f"[GSSHOP] 카테고리 스캔 상세 실패: {pid} — render JSON 추출 불가 (단종/리다이렉트 추정)"
                )
            except Exception as e:
                logger.warning(
                    f"[GSSHOP] 카테고리 스캔 상세 실패: {pid} — {type(e).__name__}: {e}"
                )
            return None

        async def _fetch(client: httpx.AsyncClient, pid: str) -> None:
            nonlocal ok_count, fail_count
            async with sem:
                try:
                    detail = await _fetch_detail(client, pid)
                    if detail is None:
                        fail_count += 1
                        GsShopSourcingClient.scan_progress["detail_fail"] = fail_count
                        return
                    c1 = detail.get("category1", "")
                    c2 = detail.get("category2", "")
                    c3 = detail.get("category3", "")
                    c4 = detail.get("category4", "")
                    if not c1:
                        fail_count += 1
                        GsShopSourcingClient.scan_progress["detail_fail"] = fail_count
                        logger.warning(
                            f"[GSSHOP] 카테고리 스캔 상세 실패: {pid} — category1 누락"
                        )
                        return
                    # GNB 대카테고리 매핑
                    gnb = self.GNB_MAP.get(c1, "")
                    parts = [gnb, c1, c2, c3, c4] if gnb else [c1, c2, c3, c4]
                    parts = [p for p in parts if p]
                    path = " > ".join(parts)
                    key = f"{path}||{gnb}||{c1}||{c2}||{c3}"
                    cat_counter[key] = cat_counter.get(key, 0) + 1
                    ok_count += 1
                    GsShopSourcingClient.scan_progress["detail_ok"] = ok_count
                except Exception as e:
                    fail_count += 1
                    GsShopSourcingClient.scan_progress["detail_fail"] = fail_count
                    logger.warning(
                        f"[GSSHOP] 카테고리 스캔 상세 실패: {pid} — {type(e).__name__}: {e}"
                    )

        # 500개씩 청크 분할 → 프록시 순차 로테이션
        chunks = [
            product_ids[i : i + CHUNK_SIZE]
            for i in range(0, len(product_ids), CHUNK_SIZE)
        ]
        for chunk_idx, chunk in enumerate(chunks):
            proxy = (
                self._proxy_pool[chunk_idx % len(self._proxy_pool)]
                if self._proxy_pool
                else None
            )
            client_kwargs: dict[str, Any] = {
                "timeout": scan_timeout,
                "follow_redirects": True,
            }
            if proxy:
                client_kwargs["proxy"] = proxy
            proxy_label = (
                proxy.split("@")[-1] if proxy and "@" in proxy else (proxy or "direct")
            )
            logger.info(
                f"[GSSHOP] 카테고리 스캔 청크 {chunk_idx + 1}/{len(chunks)}"
                f" ({len(chunk)}건) — 프록시: {proxy_label}"
            )
            async with httpx.AsyncClient(**client_kwargs) as scan_client:
                await asyncio.gather(
                    *[_fetch(scan_client, pid) for pid in chunk],
                    return_exceptions=True,
                )
        logger.info(
            f"[GSSHOP] 카테고리 스캔 상세 완료: 성공={ok_count} 실패={fail_count}"
        )
        GsShopSourcingClient.scan_progress = {}

        # 3. 카테고리 분포 집계
        categories = []
        for key, count in sorted(cat_counter.items(), key=lambda x: -x[1]):
            path, gnb, c1, c2, c3 = key.split("||")
            categories.append(
                {
                    "categoryCode": path,
                    "path": path,
                    "count": count,
                    "category1": gnb or c1,
                    "category2": c1 if gnb else c2,
                    "category3": c2 if gnb else c3,
                }
            )

        total = sum(c["count"] for c in categories)
        logger.info(
            f'[GSSHOP] 카테고리 스캔 완료: "{keyword}" → {len(categories)}개 카테고리, {total}건'
        )

        return {
            "categories": categories,
            "total": total,
            "groupCount": len(categories),
        }

    # ------------------------------------------------------------------
    # 상세 조회
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # HTTP 요청 헬퍼 (모바일 / PC 분리)
    # ------------------------------------------------------------------

    async def _fetch_mobile(self, product_id: str, proxy: str | None = None) -> str:
        """모바일 상세 페이지 HTML 반환. 실패 시 빈 문자열.

        GS샵은 삭제/품절 상품을 메인(`/index.gs`) 또는 검색결과없음
        (`/search/noResult.gs`) 페이지로 302 redirect(HTTP 200) 시킨다.
        follow_redirects=True 로 따라가면 정상 응답으로 인식되어 모든 갱신이
        in_stock 으로 고정되는 버그가 발생하므로 redirect 직접 검사로 영구
        삭제 신호를 잡는다.
        """
        url = f"{self.PRODUCT_URL}?prdid={product_id}"
        _ck: dict[str, Any] = {
            "timeout": self._timeout,
            "follow_redirects": False,
        }
        if proxy:
            _ck["proxy"] = proxy
        async with httpx.AsyncClient(**_ck) as client:
            resp = await client.get(url, headers=self._headers(mobile=True))
            if resp.status_code in (429, 403):
                retry_after = int(resp.headers.get("Retry-After", "60"))
                logger.warning(
                    f"[GSSHOP] 차단 감지 HTTP {resp.status_code}: {product_id}"
                )
                raise RateLimitError(resp.status_code, retry_after)
            if resp.status_code == 404:
                logger.warning(f"[GSSHOP] 상품 영구 삭제 감지 (404): {product_id}")
                raise ProductNotFoundError(product_id)
            # 3xx redirect — Location이 상품 상세가 아니면 영구 삭제로 간주
            if 300 <= resp.status_code < 400:
                location = resp.headers.get("Location", "") or resp.headers.get(
                    "location", ""
                )
                # 상세 페이지 외(메인/검색결과없음/카테고리 등)로 보내면 삭제 신호
                if "prdid=" not in location and "prd.gs" not in location:
                    logger.warning(
                        f"[GSSHOP] 상품 영구 삭제 감지 (redirect→{location[:80]}): {product_id}"
                    )
                    raise ProductNotFoundError(product_id)
                # 상세 → 상세 redirect는 흔치 않지만 발생 시 한 번만 따라가서 응답 확보
                follow = await client.get(
                    location
                    if location.startswith("http")
                    else f"{self.BASE_MOBILE}{location}",
                    headers=self._headers(mobile=True),
                )
                if follow.status_code == 200:
                    return follow.text
                logger.warning(
                    f"[GSSHOP] redirect 후 HTTP {follow.status_code}: {product_id}"
                )
                return ""
            if resp.status_code != 200:
                logger.warning(
                    f"[GSSHOP] 모바일 상세 HTTP {resp.status_code}: {product_id}"
                )
                return ""
            # GS샵은 모바일 UA에 redirect 대신 HTTP 200 + "DustView" 에러
            # HTML을 반환하는 경우가 있다 (예: 존재 안 하는 prdid). 마커 검출로
            # 영구 삭제 신호를 잡는다.
            # 주의: "에러 페이지" substring 마커 금지 — 정상 판매중 상품 페이지의
            # 공통 JS 주석("공통 에러 컨트롤러 ... 에러 페이지의 goBack ...")에도
            # 항상 포함되어 전상품 오삭제를 유발했다(이슈 #380, 라이브 검증 완료).
            # 진짜 삭제건은 DustView 마커 / 302 redirect / 상품명 추출 실패로 감지된다.
            body = resp.text
            if "DustView" in body:
                logger.warning(
                    f"[GSSHOP] 상품 영구 삭제 감지 (에러 페이지 본문): {product_id}"
                )
                raise ProductNotFoundError(product_id)
            return body

    async def _fetch_pc(self, product_id: str, proxy: str | None = None) -> str:
        """PC 상세 페이지 HTML 반환. 실패 시 빈 문자열 (수집 중단 X)."""
        pc_url = f"{self.BASE_PC}/prd/prd.gs?prdid={product_id}"
        try:
            _ck: dict[str, Any] = {
                "timeout": self._timeout,
                "follow_redirects": True,
            }
            if proxy:
                _ck["proxy"] = proxy
            async with httpx.AsyncClient(**_ck) as client:
                resp = await client.get(pc_url, headers=self._headers(mobile=False))
                if resp.status_code != 200:
                    return ""
                return resp.text
        except Exception as e:
            logger.debug(f"[GSSHOP] PC 상세 실패: {product_id} {e}")
            return ""

    def _parse_mobile_html(self, html: str, product_id: str) -> dict[str, Any]:
        """모바일 HTML에서 renderJson → JSON-LD → og 메타 순으로 파싱.

        모든 폴백에서 name 추출에 실패하면 영구 삭제 상품으로 간주하고
        ProductNotFoundError 를 발생시킨다 (renderJson 의 prd 필드가 비거나,
        JSON-LD/og 메타도 없는 케이스).
        """
        if not html:
            return {}
        now_iso = datetime.now(tz=timezone.utc).isoformat()
        timestamp = int(datetime.now(tz=timezone.utc).timestamp() * 1000)

        # 1순위: renderJson
        render_data = self._extract_render_json(html)
        if render_data:
            result = self._build_from_render_json(
                render_data, product_id, timestamp, now_iso
            )
            if result.get("name"):
                return result

        # 2순위: JSON-LD
        json_ld_data = self._extract_json_ld(html)
        if json_ld_data:
            result = self._build_from_json_ld(
                json_ld_data, product_id, timestamp, now_iso
            )
            if result.get("name"):
                return result

        # 3순위: og 메타 태그
        result = self._build_from_meta(html, product_id, timestamp, now_iso)
        if result.get("name"):
            return result

        # 모든 폴백 실패 — 상품이 더 이상 존재하지 않음 (메인페이지 redirect 등)
        logger.warning(
            f"[GSSHOP] 상품 영구 삭제 감지 (파싱 결과 비어있음): {product_id}"
        )
        raise ProductNotFoundError(product_id)

    # ------------------------------------------------------------------
    # 상세 조회 메인
    # ------------------------------------------------------------------

    async def get_product_detail(
        self, product_id: str, refresh_only: bool = False
    ) -> dict[str, Any]:
        """GS샵 상품 상세 정보 조회 (재시도 2회 포함).

        refresh_only=False(수집): 모바일+PC 병렬 요청 → 데이터 온전 유지
        refresh_only=True(오토튠/가격재고): 모바일만 → 빠른 가격/재고 갱신
        """
        logger.info(
            f"[GSSHOP] 상세 조회: {product_id}"
            f"{' (refresh_only)' if refresh_only else ''}"
        )

        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                _proxy = self._next_proxy()

                if refresh_only:
                    mobile_html = await self._fetch_mobile(product_id, _proxy)
                    return self._parse_mobile_html(mobile_html, product_id)
                else:
                    mobile_html, pc_html = await asyncio.gather(
                        self._fetch_mobile(product_id, _proxy),
                        self._fetch_pc(product_id, _proxy),
                    )
                    result = self._parse_mobile_html(mobile_html, product_id)
                    if result.get("name") and pc_html:
                        self._enrich_from_pc_html(result, pc_html)
                    return result

            except ProductNotFoundError:
                raise  # 재시도 없이 호출자에게 전달
            except RateLimitError as e:
                last_exc = e
                wait = min(e.retry_after, 15) if e.retry_after else (2**attempt)
                logger.warning(
                    f"[GSSHOP] 차단({e.status}) {product_id} — {wait}s 대기 후 재시도({attempt + 1}/3)"
                )
                await asyncio.sleep(wait)
            except httpx.TimeoutException as e:
                last_exc = e
                if attempt < 2:
                    wait = 1.0 * (attempt + 1)
                    logger.warning(
                        f"[GSSHOP] 타임아웃 {product_id} — {wait}s 대기 후 재시도({attempt + 1}/3)"
                    )
                    await asyncio.sleep(wait)
                else:
                    logger.error(f"[GSSHOP] 타임아웃 최종 실패: {product_id}")
                    return {}
            except Exception as e:
                logger.error(f"[GSSHOP] 상세 조회 실패: {product_id} — {e}")
                return {}

        logger.error(f"[GSSHOP] 상세 조회 3회 모두 실패: {product_id} — {last_exc}")
        return {}

    async def get_detail(self, product_id: str) -> dict[str, Any]:
        """worker 호환 상세 조회 — get_product_detail 래핑 + snake_case 변환."""
        raw = await self.get_product_detail(product_id)
        if not raw:
            return {}
        return {
            "name": raw.get("name", ""),
            "brand": raw.get("brand", ""),
            "manufacturer": raw.get("manufacturer", ""),
            "category": raw.get("category", ""),
            "category1": raw.get("category1", ""),
            "category2": raw.get("category2", ""),
            "category3": raw.get("category3", ""),
            "category4": raw.get("category4", ""),
            "images": raw.get("images", []),
            "options": raw.get("options", []),
            "detail_html": raw.get("detailHtml", ""),
            "detail_images": raw.get("detailImages", []),
            "salePrice": raw.get("salePrice", 0),
            "originalPrice": raw.get("originalPrice", 0),
            "bestBenefitPrice": raw.get("bestBenefitPrice", 0),
            "source_url": raw.get("sourceUrl", ""),
            "free_shipping": raw.get("freeShipping", False),
            "shipping_fee": 0 if raw.get("freeShipping", False) else 3000,
            "origin": raw.get("origin", ""),
            "style_code": raw.get("modelName", ""),
            "material": raw.get("material", ""),
            "color": raw.get("color", ""),
            "care_instructions": raw.get("careInstructions", ""),
            "quality_guarantee": raw.get("qualityGuarantee", ""),
        }

    # ------------------------------------------------------------------
    # PC 페이지 상품정보 보충 (제조국/색상/재질만)
    # ------------------------------------------------------------------

    def _enrich_from_pc_html(
        self,
        result: dict[str, Any],
        html: str,
    ) -> None:
        """PC HTML의 상품정보 테이블에서 누락 필드 보충 (HTTP 호출 없음).

        100개 상품 전수조사 기반 th 변형 패턴:
        - 소재: "제품 소재 (섬유의 조성...)" (55%), "소재" (12%)
        - 색상: "공통색상" (55%), "색상" (17%)
        - 제조국: "제조국" (97%)
        - 세탁방법: "세탁방법 및 취급시 주의사항" (55%), "취급시 주의사항" (20%)
        - 품질보증: "품질보증기준" (97%), "품질보증기간" (15%)
        - 품번: "품명 및 모델명" (15%)
        """
        if not html:
            return

        # 상품정보 테이블에서 th-td 추출
        tables = re.findall(r"<table[^>]*>(.*?)</table>", html, re.DOTALL)
        for table in tables:
            rows = re.findall(
                r"<th[^>]*>(.*?)</th>\s*<td[^>]*>(.*?)</td>",
                table,
                re.DOTALL,
            )
            if len(rows) < 3:
                continue
            has_spec = any(
                "소재" in r[0] or "제조" in r[0] or "색상" in r[0] for r in rows
            )
            if not has_spec:
                continue

            for th_raw, td_raw in rows:
                th = re.sub(r"<[^>]+>", "", th_raw).strip()
                td = re.sub(r"<[^>]+>", "", td_raw).strip()
                td = re.sub(r"\s+", " ", td)
                if not th or not td:
                    continue
                # 제조국
                if "제조국" in th and not result.get("origin"):
                    result["origin"] = td
                # 색상 ("공통색상", "색상")
                elif "색상" in th and not result.get("color"):
                    result["color"] = td
                # 소재/재질 ("제품 소재 (섬유의 조성...)", "소재")
                elif ("소재" in th or "재질" in th) and not result.get("material"):
                    result["material"] = td
                # 세탁방법/취급주의사항
                elif ("세탁" in th or "취급" in th) and not result.get(
                    "careInstructions"
                ):
                    result["careInstructions"] = td
                # 품질보증 ("품질보증기준", "품질보증기간")
                elif "품질보증" in th and not result.get("qualityGuarantee"):
                    result["qualityGuarantee"] = td
                # 품명 및 모델명 (품번 폴백)
                elif "모델명" in th and not result.get("modelName"):
                    result["modelName"] = td
            break

    # ------------------------------------------------------------------
    # renderJson 파싱 (1순위)
    # ------------------------------------------------------------------

    def _extract_render_json(self, html: str) -> Optional[dict[str, Any]]:
        """모바일 상세 페이지의 `var renderJson = {...}` 추출."""
        start_idx = html.find("var renderJson = ")
        if start_idx == -1:
            start_idx = html.find("var renderJson=")
        if start_idx == -1:
            return None

        json_start = html.find("{", start_idx)
        if json_start == -1:
            return None

        text = html[json_start:]
        depth = 0
        end = 0
        for i, c in enumerate(text):
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
            if depth == 0:
                end = i + 1
                break

        if end == 0:
            return None

        try:
            return json.loads(text[:end])
        except (json.JSONDecodeError, ValueError):
            logger.warning("[GSSHOP] renderJson 파싱 실패")
            return None

    def _build_from_render_json(
        self,
        data: dict[str, Any],
        product_id: str,
        timestamp: int,
        now_iso: str,
    ) -> dict[str, Any]:
        """renderJson 데이터에서 표준 상품 dict 생성."""
        prd = data.get("prd") or {}
        pmo = data.get("pmo") or {}
        prc = pmo.get("prc") or {}

        # 상품명
        name = (prd.get("exposPrdNm", "") or prd.get("prdNm", "") or "").strip()

        # 브랜드
        brand = prd.get("brandNm", "").strip()
        name_info = prd.get("nameInfo") or {}
        if not brand:
            brand = name_info.get("brandShopNm", "").strip()

        # 제조사
        manufacturer = prd.get("mnfcCo", "").strip()

        # 가격
        sale_prc = self._safe_int(prc.get("salePrc", 0))  # 정가
        gs_prc = self._safe_int(pmo.get("gsPrc", 0))  # GS가 (판매가)
        flgd_prc = self._safe_int(prc.get("flgdPrc", 0))  # 할인가
        sale_price = gs_prc or flgd_prc or sale_prc
        original_price = sale_prc or gs_prc
        discount_rate = self._safe_int(prc.get("prcDcRt", 0))
        cpn_dc_amt = self._safe_int(prc.get("cpnDcAmt", 0))  # 쿠폰할인액

        # 최대혜택가 = 판매가 - 쿠폰할인
        # GS샵 cpnDcAmt는 대개 "정가→판매가"를 만든 기본 할인액이라 이미 gsPrc(판매가)에 반영돼 있음
        # (검증: salePrc - cpnDcAmt == gsPrc). 그대로 빼면 이중차감 → 원가 과소계상(역마진).
        # 정가-쿠폰==판매가면 이미 반영된 것이므로 추가 차감하지 않음. 진짜 별도쿠폰(미반영)만 차감.
        coupon_already_in_price = (sale_prc - cpn_dc_amt) == sale_price
        if cpn_dc_amt > 0 and not coupon_already_in_price:
            best_benefit_price = sale_price - cpn_dc_amt
        else:
            best_benefit_price = sale_price
        if best_benefit_price <= 0:
            best_benefit_price = sale_price

        # 카테고리 — GNB 대카테고리 매핑 포함
        ctgr = prd.get("ctgrInfo") or {}
        category_levels = []
        lsect = (ctgr.get("lsectNm") or "").strip()
        gnb = self.GNB_MAP.get(lsect, "")
        if gnb:
            category_levels.append(gnb)
        for key in ["lsectNm", "msectNm", "ssectNm", "dsectNm"]:
            val = ctgr.get(key, "")
            if val and val.strip() not in category_levels:
                category_levels.append(val.strip())
        category_str = " > ".join(category_levels)

        # 이미지 (imgInfo 배열, 최대 9장)
        images: list[str] = []
        for img in prd.get("imgInfo") or []:
            img_url = img.get("imgUrl", "")
            if img_url and img_url not in images:
                images.append(img_url)
            if len(images) >= 9:
                break

        # 이미지 부족 시 mediaInfo에서 보충
        if len(images) < 9:
            media_info = prd.get("mediaInfo") or {}
            for img_url in media_info.get("images") or []:
                if isinstance(img_url, str) and img_url and img_url not in images:
                    images.append(img_url)
                if len(images) >= 9:
                    break

        # 상세 이미지 (prdImgDescd HTML 내 <img>)
        detail_html = prd.get("prdImgDescd", "") or ""
        detail_images = self._extract_detail_images_from_html(detail_html)

        # 품절 판단
        prd_sale_st = prd.get("prdSaleSt", "Y")
        is_out_of_stock = prd_sale_st != "Y"

        # 옵션 (attrTypList) — 전체 품절 여부를 옵션에 반영해야 stock_changed 감지 가능
        options = self._parse_options_from_render(prd, is_out_of_stock=is_out_of_stock)

        if not is_out_of_stock and options:
            is_out_of_stock = all(opt.get("isSoldOut", False) for opt in options)

        sale_status = "sold_out" if is_out_of_stock else "in_stock"

        # 배송
        free_shipping = prd.get("freeDlvFlg") == "Y" or prd.get("dlvcAmt", 0) == 0
        quick_delivery = prd.get("quickDlvFlg") == "Y"

        # 원산지 (orgp 필드 — "필수정보 참조"인 경우 빈 값)
        origin = prd.get("orgp", "").strip()
        if "참조" in origin or "상세" in origin:
            origin = ""

        # 모델명 (품번) — renderJson 없으면 상품명에서 영숫자 코드 추출
        model_name = prd.get("modelNm", "").strip()
        if not model_name and name:
            _code_m = re.search(r"[A-Z]\w{5,}", name)
            if _code_m:
                model_name = _code_m.group(0)

        return {
            "id": f"col_gsshop_{product_id}_{timestamp}",
            "sourceSite": "GSSHOP",
            "siteProductId": str(product_id),
            "sourceUrl": f"{self.BASE_PC}/prd/prd.gs?prdid={product_id}",
            "name": name,
            "brand": brand,
            "manufacturer": manufacturer,
            "category": category_str,
            "category1": category_levels[0] if len(category_levels) > 0 else "",
            "category2": category_levels[1] if len(category_levels) > 1 else "",
            "category3": category_levels[2] if len(category_levels) > 2 else "",
            "category4": category_levels[3] if len(category_levels) > 3 else "",
            "images": images[:9],
            "detailImages": detail_images,
            "detailHtml": detail_html,
            "options": options,
            "originalPrice": original_price,
            "salePrice": sale_price,
            "bestBenefitPrice": best_benefit_price,
            "discountRate": discount_rate,
            "saleStatus": sale_status,
            "isOutOfStock": is_out_of_stock,
            "freeShipping": free_shipping,
            "sameDayDelivery": quick_delivery,
            "origin": origin,
            "modelName": model_name,
            "material": "",
            "color": "",
            "collectedAt": now_iso,
            "updatedAt": now_iso,
            "status": "collected",
        }

    def _parse_options_from_render(
        self, prd: dict[str, Any], *, is_out_of_stock: bool = False
    ) -> list[dict[str, Any]]:
        """renderJson.prd의 attrTypList에서 옵션 추출.

        GS샵 옵션은 `attrTypVal` 필드에 구분자 0x08(\\b)로 연결된 형식:
          예) "블랙\\b090(S)" → 색상=블랙, 사이즈=090(S)
        is_out_of_stock: 상품 전체 품절 여부 → 옵션에 반영해야 stock_changed 감지 가능
        """
        options: list[dict[str, Any]] = []
        attr_list = prd.get("attrTypList") or []
        nm1 = prd.get("attrTypNm1", "")  # 예: "색상"
        nm2 = prd.get("attrTypNm2", "")  # 예: "사이즈"

        for attr in attr_list:
            raw_val = attr.get("attrTypVal", "")
            parts = raw_val.split("\x08")  # 0x08 = 백스페이스(구분자)
            opt_name = " / ".join(p.strip() for p in parts if p.strip())

            # stockFlg: Y=재고관리중(한정), N=재고관리안함(무제한/항상판매)
            # 옵션 레벨 품절은 상품 전체 prdSaleSt로 결정
            stock_flg = attr.get("stockFlg", "N")

            options.append(
                {
                    "name": opt_name,
                    "price": 0,  # GS샵 옵션은 추가가격 없음 (동일가)
                    "stock": 0 if is_out_of_stock else 99,
                    "isSoldOut": is_out_of_stock,
                    "attrPrdCd": attr.get("attrPrdCd"),
                }
            )

        return options

    # ------------------------------------------------------------------
    # JSON-LD 파싱 (2순위 폴백)
    # ------------------------------------------------------------------

    def _extract_json_ld(self, html: str) -> Optional[dict[str, Any]]:
        """JSON-LD (schema.org Product) 추출."""
        pattern = re.compile(
            r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
            re.DOTALL,
        )
        for m in pattern.finditer(html):
            try:
                data = json.loads(m.group(1))
                # @graph 배열에서 Product 타입 찾기
                graph = data.get("@graph") or [data]
                for item in graph:
                    if item.get("@type") == "Product":
                        return item
            except (json.JSONDecodeError, TypeError):
                continue
        return None

    def _build_from_json_ld(
        self,
        ld: dict[str, Any],
        product_id: str,
        timestamp: int,
        now_iso: str,
    ) -> dict[str, Any]:
        """JSON-LD 데이터에서 표준 상품 dict 생성."""
        name = ld.get("name", "").strip()
        brand_obj = ld.get("brand") or {}
        brand = brand_obj.get("name", "") if isinstance(brand_obj, dict) else ""
        images_raw = ld.get("image") or []
        images = images_raw if isinstance(images_raw, list) else [images_raw]

        offers = ld.get("offers") or {}
        price = self._safe_int(offers.get("price", 0))
        availability = offers.get("availability", "")
        is_out_of_stock = "OutOfStock" in availability

        rating = ld.get("aggregateRating") or {}

        return {
            "id": f"col_gsshop_{product_id}_{timestamp}",
            "sourceSite": "GSSHOP",
            "siteProductId": str(product_id),
            "sourceUrl": f"{self.BASE_PC}/prd/prd.gs?prdid={product_id}",
            "name": name,
            "brand": brand,
            "manufacturer": "",
            "category": "",
            "category1": "",
            "category2": "",
            "category3": "",
            "category4": "",
            "images": images[:9],
            "detailImages": [],
            "detailHtml": "",
            "options": [],
            "originalPrice": price,
            "salePrice": price,
            "bestBenefitPrice": price,
            "discountRate": 0,
            "saleStatus": "sold_out" if is_out_of_stock else "in_stock",
            "isOutOfStock": is_out_of_stock,
            "freeShipping": False,
            "sameDayDelivery": False,
            "origin": "",
            "collectedAt": now_iso,
            "updatedAt": now_iso,
            "status": "collected",
        }

    # ------------------------------------------------------------------
    # og 메타 태그 파싱 (3순위 폴백)
    # ------------------------------------------------------------------

    def _build_from_meta(
        self,
        html: str,
        product_id: str,
        timestamp: int,
        now_iso: str,
    ) -> dict[str, Any]:
        """og 메타 태그에서 최소 정보 추출."""
        name = self._extract_meta(html, "og:title") or ""
        image = self._normalize_image(self._extract_meta(html, "og:image") or "")
        images = [image] if image else []

        return {
            "id": f"col_gsshop_{product_id}_{timestamp}",
            "sourceSite": "GSSHOP",
            "siteProductId": str(product_id),
            "sourceUrl": f"{self.BASE_PC}/prd/prd.gs?prdid={product_id}",
            "name": name.replace("[GS SHOP] ", "").replace(" - GS SHOP", "").strip(),
            "brand": "",
            "manufacturer": "",
            "category": "",
            "category1": "",
            "category2": "",
            "category3": "",
            "category4": "",
            "images": images,
            "detailImages": [],
            "detailHtml": "",
            "options": [],
            "originalPrice": 0,
            "salePrice": 0,
            "bestBenefitPrice": 0,
            "discountRate": 0,
            "saleStatus": "in_stock",
            "isOutOfStock": False,
            "freeShipping": False,
            "sameDayDelivery": False,
            "origin": "",
            "collectedAt": now_iso,
            "updatedAt": now_iso,
            "status": "collected",
        }

    # ------------------------------------------------------------------
    # 상세 이미지 헬퍼
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_detail_images_from_html(
        desc_html: str,
    ) -> list[str]:
        """상세 설명 HTML에서 이미지 URL 추출."""
        if not desc_html:
            return []

        detail_images: list[str] = []
        # src + data-src + data-lazy + data-original 전부 캡처 (lazy-load 대응)
        pattern = re.compile(
            r'<img[^>]+(?:src|data-src|data-lazy|data-original)=["\']([^"\']+)["\']',
            re.I,
        )
        for m in pattern.finditer(desc_html):
            src = m.group(1).strip()
            if not src:
                continue
            # 프로토콜 보정
            if src.startswith("//"):
                src = f"https:{src}"
            # 아이콘/버튼 이미지 제외
            if "icon" in src.lower() or "btn_" in src.lower():
                continue
            if src not in detail_images:
                detail_images.append(src)

        return detail_images

    # ------------------------------------------------------------------
    # 공통 헬퍼
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_image(url: str) -> str:
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
            rf'<meta[^>]+(?:property|name)="{re.escape(prop)}"'
            rf'[^>]+content="([^"]*)"'
        )
        m = re.search(pattern, html, re.IGNORECASE)
        if m:
            return m.group(1)
        # content가 먼저 오는 경우
        pattern2 = (
            rf'<meta[^>]+content="([^"]*)"'
            rf'[^>]+(?:property|name)="{re.escape(prop)}"'
        )
        m2 = re.search(pattern2, html, re.IGNORECASE)
        return m2.group(1) if m2 else None

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
