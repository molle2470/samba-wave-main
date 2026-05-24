"""카페24 Admin API 클라이언트 — 상품 등록/수정/삭제 + 카테고리 조회.

인증 방식: OAuth2 Authorization Code Flow
- mallId별 Base URL: https://{mallId}.cafe24api.com/api/v2/admin/
- Access Token 유효기간: 2시간, Refresh Token: 2주
- Rate Limit: Leaky Bucket 40/2초
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

import httpx

from backend.utils.logger import logger
from backend.domain.samba.proxy.notice_utils import detect_notice_group


def _build_notice_html(product: dict[str, Any]) -> str:
    """상품정보제공고시 HTML 테이블 생성 (상세페이지 하단 삽입용)."""
    group = detect_notice_group(product)
    fallback = "상품 상세페이지 참조"

    _DEFAULT_CAUTION: dict[str, str] = {
        "wear": "세탁 시 뒤집어서 단독 손세탁, 표백제 사용 금지, 직사광선을 피해 그늘에서 건조",
        "shoes": "물세탁 불가, 직사광선 및 고온 다습한 곳 보관 금지, 벤젠/신나 등 화학제품 사용 금지",
        "bag": "직사광선 및 고온 다습한 환경을 피해 보관, 마찰에 의한 색 이염 주의",
        "accessories": "직사광선 및 습기를 피해 보관, 화학제품 접촉 주의",
    }

    brand = product.get("brand") or ""
    manufacturer = product.get("manufacturer") or brand or fallback
    origin = product.get("origin") or fallback
    material = product.get("material") or fallback
    color = product.get("color") or fallback
    caution = (
        product.get("care_instructions")
        or product.get("careInstructions")
        or _DEFAULT_CAUTION.get(group, fallback)
    )
    size_fallback = "상세 이미지 참조"
    pack_date = "주문 후 개별포장 발송"
    warranty = "제품 하자 시 소비자분쟁해결기준(공정거래위원회 고시)에 따라 보상"
    as_contact = f"{brand} 고객센터" if brand else fallback

    # 카테고리별 행 구성
    if group in ("wear", "shoes"):
        rows = [
            ("제품 소재", material),
            ("색상", color),
            ("치수", size_fallback),
            ("제조자(수입자)", manufacturer),
            ("제조국", origin),
            ("세탁방법 및 취급시 주의사항", caution),
            ("제조연월", pack_date),
            ("품질보증기준", warranty),
            ("A/S 책임자와 전화번호", as_contact),
        ]
    elif group == "bag":
        rows = [
            ("종류", fallback),
            ("소재", material),
            ("색상", color),
            ("크기", size_fallback),
            ("제조자(수입자)", manufacturer),
            ("제조국", origin),
            ("세탁방법 및 취급시 주의사항", caution),
            ("제조연월", pack_date),
            ("품질보증기준", warranty),
            ("A/S 책임자와 전화번호", as_contact),
        ]
    elif group == "accessories":
        rows = [
            ("종류", fallback),
            ("소재", material),
            ("치수", size_fallback),
            ("제조자(수입자)", manufacturer),
            ("제조국", origin),
            ("취급시 주의사항", caution),
            ("품질보증기준", warranty),
            ("A/S 책임자와 전화번호", as_contact),
        ]
    else:
        rows = [
            ("품명 및 모델명", product.get("name") or fallback),
            ("제조자(수입자)", manufacturer),
            ("제조국", origin),
            ("제품 소재", material),
            ("A/S 책임자와 전화번호", as_contact),
        ]

    row_html = "".join(
        f'<tr><td style="background:#f8f8f8;padding:8px 12px;border:1px solid #ddd;'
        f'font-weight:bold;white-space:nowrap;width:160px;">{k}</td>'
        f'<td style="padding:8px 12px;border:1px solid #ddd;">{v}</td></tr>'
        for k, v in rows
    )

    return (
        '<div style="max-width:760px;margin:20px auto 0;">'
        '<table style="width:100%;border-collapse:collapse;font-size:13px;">'
        '<thead><tr><th colspan="2" style="background:#333;color:#fff;padding:10px 12px;'
        'text-align:left;font-size:14px;">상품정보 제공고시</th></tr></thead>'
        f"<tbody>{row_html}</tbody>"
        "</table></div>"
    )


def _convert_to_jpeg(image_bytes: bytes) -> bytes:
    """WebP/AVIF 이미지를 JPEG로 변환 (카페24 WebP 미지원 대응)."""
    import io
    from PIL import Image

    img = Image.open(io.BytesIO(image_bytes))
    if img.mode in ("RGBA", "P", "LA"):
        img = img.convert("RGB")
    output = io.BytesIO()
    img.save(output, format="JPEG", quality=90)
    return output.getvalue()


class Cafe24ApiError(Exception):
    """카페24 API 에러.

    - API 응답 에러: Cafe24ApiError(status, code, message)
    - 내부 검증/에러: Cafe24ApiError("메시지") — status=0, code=""
    """

    def __init__(
        self,
        status: int | str,
        code: str | None = None,
        message: str | None = None,
    ):
        if code is None and message is None:
            # 단일 메시지 형태 호출
            self.status = 0
            self.code = ""
            super().__init__(str(status))
        else:
            self.status = int(status) if isinstance(status, (int, str)) else 0
            self.code = code or ""
            super().__init__(f"[{status}] {code}: {message}")


# mall_id별 토큰 공유 캐시 — 동시 요청 간 갱신된 토큰 재사용
_token_cache: dict[str, dict[str, str]] = {}
# mall_id별 토큰 갱신 Lock — 동시 refresh_token 사용 방지
_refresh_locks: dict[str, asyncio.Lock] = {}
# mall_id별 카테고리 캐시 — 5분간 유지 (API 중복 호출 방지)
_category_cache: dict[str, dict] = {}
# mall_id별 카테고리 생성 Lock — 동시 생성 중복 방지
_category_create_locks: dict[str, asyncio.Lock] = {}


class Cafe24Client:
    """카페24 Admin REST API 클라이언트."""

    def __init__(
        self,
        mall_id: str,
        client_id: str,
        client_secret: str,
        access_token: str = "",
        refresh_token: str = "",
    ):
        self.mall_id = mall_id
        self.client_id = client_id
        self.client_secret = client_secret
        # 공유 캐시에 최신 토큰이 있으면 우선 사용 (다른 병렬 요청이 이미 갱신했을 수 있음)
        cached = _token_cache.get(mall_id, {})
        self.access_token = cached.get("access_token") or access_token
        self.refresh_token = cached.get("refresh_token") or refresh_token
        self.base_url = f"https://{mall_id}.cafe24api.com/api/v2/admin"

    # ── 인증 ────────────────────────────────────────

    async def ensure_token(self) -> str:
        """Access Token이 없거나 만료 시 Refresh Token으로 갱신."""
        if self.access_token:
            return self.access_token
        if not self.refresh_token:
            raise Cafe24ApiError(
                401, "NO_TOKEN", "access_token과 refresh_token이 모두 없습니다"
            )
        return await self._refresh_access_token()

    @staticmethod
    async def exchange_code(
        mall_id: str,
        client_id: str,
        client_secret: str,
        code: str,
        redirect_uri: str,
    ) -> dict[str, str]:
        """Authorization Code → Access/Refresh Token 교환."""
        import base64

        url = f"https://{mall_id}.cafe24api.com/api/v2/oauth/token"
        credentials = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                url,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
                headers={
                    "Authorization": f"Basic {credentials}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
        body = resp.json()
        if resp.status_code != 200:
            raise Cafe24ApiError(
                resp.status_code,
                body.get("error", "TOKEN_ERROR"),
                body.get("error_description", "코드 교환 실패"),
            )
        logger.info(f"[카페24] OAuth 코드 교환 성공: mall={mall_id}")
        return {
            "access_token": body["access_token"],
            "refresh_token": body.get("refresh_token", ""),
        }

    async def _refresh_access_token(self) -> str:
        """Refresh Token으로 Access Token 재발급 — mall_id별 Lock으로 동시 갱신 방지."""
        lock = _refresh_locks.setdefault(self.mall_id, asyncio.Lock())
        async with lock:
            # 락 대기 후 캐시 재확인 — 다른 요청이 이미 갱신했으면 재사용
            cached = _token_cache.get(self.mall_id, {})
            if cached.get("access_token"):
                self.access_token = cached["access_token"]
                self.refresh_token = cached.get("refresh_token", self.refresh_token)
                logger.info(
                    f"[카페24] 토큰 캐시 재사용 (병렬 요청): mall={self.mall_id}"
                )
                return self.access_token

            url = f"https://{self.mall_id}.cafe24api.com/api/v2/oauth/token"
            data = {
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
            }
            import base64

            credentials = base64.b64encode(
                f"{self.client_id}:{self.client_secret}".encode()
            ).decode()

            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    url,
                    data=data,
                    headers={
                        "Authorization": f"Basic {credentials}",
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                )
            body = resp.json()
            if resp.status_code != 200:
                raise Cafe24ApiError(
                    resp.status_code,
                    body.get("error", "TOKEN_ERROR"),
                    body.get("error_description", "토큰 갱신 실패"),
                )
            self.access_token = body["access_token"]
            self.refresh_token = body.get("refresh_token", self.refresh_token)
            # 공유 캐시 업데이트 — 이후 병렬 요청들이 재사용
            _token_cache[self.mall_id] = {
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
            }
            logger.info(f"[카페24] 토큰 갱신 성공: mall={self.mall_id}")
            return self.access_token

    # ── 공통 API 호출 ──────────────────────────────

    async def _call_api(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        params: dict | None = None,
        retry_on_token: bool = True,
    ) -> dict[str, Any]:
        """공통 API 호출 — Rate Limit 대응 + 토큰 자동 갱신."""
        token = await self.ensure_token()
        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-Cafe24-Api-Version": "2026-03-01",
        }
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.request(
                method, url, json=body, params=params, headers=headers
            )

        # Rate Limit 모니터링
        call_limit = resp.headers.get("X-Api-Call-Limit", "")
        remaining = resp.headers.get("x-ratelimit-remaining", "")
        if call_limit:
            logger.debug(f"[카페24] Rate: {call_limit}, remaining={remaining}")

        # 429 Too Many Requests → 대기 후 재시도
        if resp.status_code == 429:
            logger.warning("[카페24] Rate Limit 초과 → 2초 대기 후 재시도")
            await asyncio.sleep(2)
            return await self._call_api(
                method, path, body, params, retry_on_token=False
            )

        # 401 → 토큰 갱신 후 재시도 (1회만)
        if resp.status_code == 401 and retry_on_token and self.refresh_token:
            logger.warning(f"[카페24] 401 응답 본문: {resp.text}")
            logger.info("[카페24] 401 → 캐시 무효화 후 토큰 갱신 재시도")
            self.access_token = ""
            # 공유 캐시 무효화 — 만료된 토큰이 재사용되지 않도록
            if self.mall_id in _token_cache:
                _token_cache[self.mall_id]["access_token"] = ""
            await self.ensure_token()
            return await self._call_api(
                method, path, body, params, retry_on_token=False
            )

        data = resp.json()
        if resp.status_code >= 400:
            error = data.get("error", {}) if isinstance(data.get("error"), dict) else {}
            raise Cafe24ApiError(
                resp.status_code,
                error.get("code", str(resp.status_code)),
                error.get("message", data.get("error_description", str(data))),
            )
        return data

    # ── 카테고리 ────────────────────────────────────

    async def get_categories(self, shop_no: int = 1) -> list[dict[str, Any]]:
        """카테고리 전체 목록 조회 (트리 구조) — 5분 캐시 적용."""
        import time

        cache_key = f"{self.mall_id}:{shop_no}"
        cached = _category_cache.get(cache_key)
        if cached and cached["expires"] > time.time():
            return cached["cats"]

        result: list[dict[str, Any]] = []
        offset = 0
        limit = 100
        while True:
            data = await self._call_api(
                "GET",
                "/categories",
                params={"shop_no": shop_no, "limit": limit, "offset": offset},
            )
            cats = data.get("categories", [])
            result.extend(cats)
            if len(cats) < limit:
                break
            offset += limit

        _category_cache[cache_key] = {"cats": result, "expires": time.time() + 300}
        return result

    def _invalidate_category_cache(self, shop_no: int = 1) -> None:
        """카테고리 생성 후 캐시 무효화."""
        cache_key = f"{self.mall_id}:{shop_no}"
        _category_cache.pop(cache_key, None)

    async def create_category(
        self,
        name: str,
        parent_no: int = 0,
        shop_no: int = 1,
        use_main: bool = False,
    ) -> int:
        """카테고리 생성 → category_no 반환.

        use_main=True: 대분류(자사몰 상단 노출) → use_display + use_main 활성화
        use_main=False: 중/소분류 → use_display만 활성화
        """
        request_body: dict = {
            "category_name": name,
            "parent_category_no": parent_no,
            "use_display": "T",
        }
        if use_main:
            request_body["use_main"] = "T"

        data = await self._call_api(
            "POST",
            "/categories",
            body={"shop_no": shop_no, "request": request_body},
        )
        cat = data.get("category", {})
        cno = int(cat.get("category_no", 0))
        logger.info(
            f"[카페24] 카테고리 생성: {name} (no={cno}, parent={parent_no}, use_main={use_main})"
        )
        return cno

    async def get_or_create_category_chain(
        self,
        levels: list[str],
        shop_no: int = 1,
        existing_cats: list | None = None,
    ) -> int:
        """소싱 카테고리 계층 → 카페24 category_no (없으면 자동 생성).

        예: ["신발", "스포츠화", "러닝화"] → 해당 말단 category_no 반환
        이미 존재하면 Lock 없이 빠른 반환, 생성 필요 시에만 Lock 획득.
        """
        levels = [lv.strip() for lv in levels if lv and lv.strip()]
        if not levels:
            return 0

        def _build_cat_map(cats: list) -> dict[tuple[str, int], int]:
            m: dict[tuple[str, int], int] = {}
            for c in cats:
                pno = int(c.get("parent_category_no") or 0)
                if pno == 1:
                    pno = 0
                cname = (c.get("category_name") or "").strip()
                cno = int(c.get("category_no", 0))
                if cname and cno:
                    m[(cname, pno)] = cno
            return m

        def _find_chain(cat_map: dict) -> int | None:
            """모든 레벨이 이미 존재하면 말단 category_no 반환, 없으면 None."""
            parent_no = 0
            for level_name in levels:
                no = cat_map.get((level_name, parent_no))
                if not no:
                    return None
                parent_no = no
            return parent_no

        # ── 1단계: Lock 없이 빠른 조회 (대부분 여기서 끝남) ──
        all_cats = await self.get_categories(shop_no)
        cat_map = _build_cat_map(all_cats)
        found = _find_chain(cat_map)
        if found:
            return found

        # ── 2단계: 없을 때만 Lock 획득 후 생성 ──
        lock = _category_create_locks.setdefault(self.mall_id, asyncio.Lock())
        async with lock:
            # Lock 대기 후 재확인 — 다른 요청이 이미 생성했을 수 있음
            all_cats = await self.get_categories(shop_no)
            cat_map = _build_cat_map(all_cats)
            found = _find_chain(cat_map)
            if found:
                return found

            # 실제 생성 — 새 카테고리는 캐시에 직접 추가 (API 전파 지연 우회)
            parent_no = 0
            new_entries: list[dict] = []
            for idx, level_name in enumerate(levels):
                key = (level_name, parent_no)
                if key in cat_map:
                    parent_no = cat_map[key]
                else:
                    is_top_level = idx == 0
                    new_no = await self.create_category(
                        level_name, parent_no, shop_no, use_main=is_top_level
                    )
                    if not new_no:
                        logger.warning(
                            f"[카페24] 카테고리 생성 실패: {level_name} (parent={parent_no})"
                        )
                        break
                    cat_map[key] = new_no
                    # 생성된 카테고리를 즉시 캐시에 추가 (다음 Lock 대기자가 재사용)
                    new_entries.append(
                        {
                            "category_no": new_no,
                            "category_name": level_name,
                            "parent_category_no": parent_no if parent_no != 0 else 1,
                        }
                    )
                    parent_no = new_no

            if new_entries:
                import time

                cache_key = f"{self.mall_id}:{shop_no}"
                cached = _category_cache.get(cache_key)
                if cached:
                    _category_cache[cache_key]["cats"] = cached["cats"] + new_entries
                else:
                    _category_cache[cache_key] = {
                        "cats": all_cats + new_entries,
                        "expires": time.time() + 300,
                    }

            return parent_no

    async def link_product_to_category(
        self,
        product_no: int,
        category_no: int,
        shop_no: int = 1,
    ) -> dict[str, Any]:
        """상품을 카테고리에 연결 (POST /categories/{no}/products)."""
        return await self._call_api(
            "POST",
            f"/categories/{category_no}/products",
            body={
                "shop_no": shop_no,
                "request": {
                    "product_no": [product_no],
                },
            },
        )

    # ── 상품 CRUD ──────────────────────────────────

    async def register_product(self, payload: dict[str, Any]) -> dict[str, Any]:
        """상품 신규 등록."""
        data = await self._call_api("POST", "/products", body=payload)
        product = data.get("product", {})
        product_no = product.get("product_no")
        logger.info(f"[카페24] 상품 등록 성공: product_no={product_no}")
        return product

    async def update_product(
        self, product_no: int, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """상품 수정."""
        data = await self._call_api("PUT", f"/products/{product_no}", body=payload)
        logger.info(f"[카페24] 상품 수정 성공: product_no={product_no}")
        return data.get("product", {})

    async def delete_product(self, product_no: int) -> dict[str, Any]:
        """상품 삭제 (마켓에서 완전 삭제)."""
        data = await self._call_api("DELETE", f"/products/{product_no}")
        logger.info(f"[카페24] 상품 삭제 성공: product_no={product_no}")
        return data

    async def get_product(self, product_no: int) -> dict[str, Any]:
        """상품 상세 조회."""
        data = await self._call_api("GET", f"/products/{product_no}")
        return data.get("product", {})

    async def find_product_by_custom_code(self, custom_code: str) -> int | None:
        """custom_product_code로 기존 상품 검색 → product_no 반환 (없으면 None)."""
        if not custom_code:
            return None
        try:
            data = await self._call_api(
                "GET",
                "/products",
                params={"custom_product_code": custom_code, "limit": 1},
            )
            products = data.get("products", [])
            if products:
                pno = products[0].get("product_no")
                logger.info(
                    f"[카페24][중복체크] custom_code={custom_code} → 기존 product_no={pno}"
                )
                return pno
            return None
        except Exception as e:
            logger.warning(f"[카페24][중복체크] 검색 실패: {e}")
            return None

    async def update_product_seo(
        self, product_no: int, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """상품 SEO 설정 업데이트."""
        data = await self._call_api("PUT", f"/products/{product_no}/seo", body=payload)
        return data.get("seo", {})

    # ── 주문 조회 ──────────────────────────────

    async def get_orders(self, days: int = 7) -> list[dict[str, Any]]:
        """최근 N일간 주문 목록 조회.

        카페24 Admin API: GET /orders
        - start_date / end_date: YYYY-MM-DD 형식
        - limit: 최대 100
        """
        from datetime import datetime, timedelta, timezone

        kst = timezone(timedelta(hours=9))
        now = datetime.now(kst)
        start = now - timedelta(days=min(days, 89))
        start_date = start.strftime("%Y-%m-%d")
        end_date = now.strftime("%Y-%m-%d")

        logger.info(f"[카페24] 주문 조회 시작: {start_date} ~ {end_date}")

        all_orders: list[dict[str, Any]] = []
        offset = 0
        limit = 100

        while True:
            try:
                data = await self._call_api(
                    "GET",
                    "/orders",
                    params={
                        "shop_no": 1,
                        "start_date": start_date,
                        "end_date": end_date,
                        "limit": limit,
                        "offset": offset,
                        "embed": "items,buyer,receivers",
                    },
                )
            except Exception as e:
                logger.warning(f"[카페24] 주문 조회 실패 (offset={offset}): {e}")
                break

            orders = data.get("orders", [])
            if not orders:
                break

            # 배송지/연락처는 목록 API에서 미지원 → /receivers 서브리소스로 보충
            for order in orders:
                order_id = order.get("order_id")
                if not order_id:
                    continue
                try:
                    rcv_data = await self._call_api(
                        "GET",
                        f"/orders/{order_id}/receivers",
                        params={"shop_no": 1},
                    )
                    receivers = rcv_data.get("receivers") or []
                    if receivers:
                        rcv = receivers[0]
                        order["shipping_address"] = {
                            "name": rcv.get("name") or "",
                            "cellphone": rcv.get("cellphone") or "",
                            "phone": rcv.get("phone") or "",
                            "address1": rcv.get("address1") or "",
                            "address2": rcv.get("address2") or "",
                            "zipcode": rcv.get("zipcode") or "",
                        }
                        order["buyer_cellphone"] = (
                            rcv.get("cellphone") or rcv.get("phone") or ""
                        )
                except Exception as e:
                    logger.warning(f"[카페24] 수령인 조회 실패 ({order_id}): {e}")

            all_orders.extend(orders)
            logger.info(f"[카페24] 주문 {len(orders)}건 수집 (누적: {len(all_orders)})")

            if len(orders) < limit:
                break
            offset += limit

        return all_orders

    async def get_boards(self) -> list[dict[str, Any]]:
        """게시판 목록 조회 (mall.read_community 스코프)."""
        try:
            data = await self._call_api(
                "GET", "/boards", params={"shop_no": 1, "limit": 100}
            )
            return data.get("boards", [])
        except Exception as e:
            logger.warning(f"[카페24] 게시판 목록 조회 실패: {e}")
            return []

    async def get_product_inquiries(self, days: int = 30) -> list[dict[str, Any]]:
        """최근 N일간 전체 상품문의 조회.

        1차 시도: GET /inquiries (mall.read_inquiry — 일반 앱 미지원)
        2차 시도: GET /boards/{board_no}/articles (mall.read_community — 상품문의 게시판 우회)
        """
        from datetime import datetime, timedelta, timezone

        kst = timezone(timedelta(hours=9))
        now = datetime.now(kst)
        start = now - timedelta(days=min(days, 90))
        start_date = start.strftime("%Y-%m-%d")
        end_date = now.strftime("%Y-%m-%d")

        logger.info(f"[카페24] 상품문의 조회: {start_date} ~ {end_date}")

        # 1차: /inquiries 시도
        all_inquiries: list[dict[str, Any]] = []
        offset = 0
        limit = 100
        inquiries_api_ok = True

        while True:
            try:
                data = await self._call_api(
                    "GET",
                    "/inquiries",
                    params={
                        "shop_no": 1,
                        "start_date": start_date,
                        "end_date": end_date,
                        "limit": limit,
                        "offset": offset,
                    },
                )
                inquiries = data.get("inquiries", [])
                if not inquiries:
                    break
                all_inquiries.extend(inquiries)
                if len(inquiries) < limit:
                    break
                offset += limit
            except Exception as e:
                logger.warning(f"[카페24] /inquiries 실패 ({e}) → 게시판 API 우회 시도")
                inquiries_api_ok = False
                break

        if all_inquiries:
            return all_inquiries

        if not inquiries_api_ok:
            # 2차: 게시판 목록에서 상품문의 게시판 찾기
            boards = await self.get_boards()
            logger.info(
                f"[카페24] 게시판 목록: {[b.get('board_name') for b in boards]}"
            )

            # 상품문의 관련 게시판 키워드
            inquiry_keywords = ["문의", "inquiry", "상품문의", "q&a", "qa"]
            inquiry_boards = [
                b
                for b in boards
                if any(
                    kw in (b.get("board_name") or "").lower() for kw in inquiry_keywords
                )
            ]

            if not inquiry_boards:
                logger.warning("[카페24] 상품문의 게시판을 찾을 수 없습니다.")
                return []

            for board in inquiry_boards:
                board_no = board.get("board_no")
                board_name = board.get("board_name")
                logger.info(
                    f"[카페24] 게시판 {board_name}(no={board_no})에서 문의 조회"
                )

                offset = 0
                while True:
                    try:
                        data = await self._call_api(
                            "GET",
                            f"/boards/{board_no}/articles",
                            params={
                                "shop_no": 1,
                                "start_date": start_date,
                                "end_date": end_date,
                                "limit": limit,
                                "offset": offset,
                            },
                        )
                        articles = data.get("articles", [])
                        if not articles:
                            break
                        # articles를 inquiries 형식으로 변환
                        for art in articles:
                            all_inquiries.append(
                                {
                                    "inquiry_no": art.get("article_no"),
                                    "board_no": board_no,
                                    "writer_name": art.get("writer")
                                    or art.get("member_id")
                                    or "고객",
                                    "writer_email": art.get("member_id") or "",
                                    "title": art.get("title") or "",
                                    "content": art.get("content") or "",
                                    "created_date": art.get("created_date")
                                    or art.get("created_at")
                                    or "",
                                    "reply_content": art.get("reply_content") or "",
                                    "reply_status": "R"
                                    if art.get("reply_content")
                                    else "N",
                                    "product_no": art.get("product_no"),
                                    "product_name": art.get("product_name") or "",
                                    "_source_board": board_name,
                                }
                            )
                        if len(articles) < limit:
                            break
                        offset += limit
                    except Exception as e:
                        logger.warning(
                            f"[카페24] 게시판({board_name}) 문의 조회 실패: {e}"
                        )
                        break

        logger.info(f"[카페24] 상품문의 총 {len(all_inquiries)}건 수집")
        return all_inquiries

    async def cancel_order(
        self,
        order_id: str,
        reason: str = "공급처 품절",
        reason_type: str = "H",
    ) -> dict[str, Any]:
        """주문 전체 취소 (PG 자동 취소 + 재고 복구).

        - reason_type: A(고객변심) B(배송지연) C(배송불가) D(포장불량) E(상품불만족)
                       F(상품정보불일치) G(서비스불만족) H(품절) I(기타)
        """
        # 1) 주문 항목 조회 — 취소 시 order_item_code 필요
        try:
            items_data = await self._call_api(
                "GET",
                f"/orders/{order_id}/items",
                params={"shop_no": 1},
            )
            raw_items = items_data.get("items") or []
        except Exception as e:
            raise Cafe24ApiError(f"주문 항목 조회 실패: {e}")

        if not raw_items:
            raise Cafe24ApiError(f"주문 {order_id}에 항목이 없습니다")

        cancel_items = [
            {
                "order_item_code": it.get("order_item_code"),
                "quantity": int(it.get("quantity") or 1),
            }
            for it in raw_items
            if it.get("order_item_code")
        ]

        if not cancel_items:
            raise Cafe24ApiError("취소 가능한 order_item_code가 없습니다")

        # 2) 취소 요청
        body = {
            "request": {
                "shop_no": 1,
                "status": "canceled",
                "payment_gateway_cancel": "T",
                "recover_inventory": "T",
                "reason": reason,
                "claim_reason_type": reason_type,
                "refund_method_code": ["F"],
                "items": cancel_items,
            }
        }
        return await self._call_api(
            "POST",
            f"/orders/{order_id}/cancellation",
            body=body,
        )

    async def get_carriers(self) -> list[dict[str, Any]]:
        """택배사 목록 조회 (carrier_code/carrier_name)."""
        try:
            data = await self._call_api("GET", "/carriers", params={"shop_no": 1})
        except Exception as e:
            raise Cafe24ApiError(f"택배사 목록 조회 실패: {e}")
        return data.get("carrier_list") or data.get("carriers") or []

    async def register_tracking(
        self,
        order_id: str,
        shipping_company_name: str,
        tracking_no: str,
    ) -> dict[str, Any]:
        """송장번호 등록 (자동으로 배송중 상태로 전환).

        shipping_company_name: 한글 택배사명 (예: '롯데택배')
        """
        if not shipping_company_name or not tracking_no:
            raise Cafe24ApiError("택배사/송장번호는 필수입니다")

        # 택배사 코드 매핑 (정규화 후 부분일치)
        # "롯데택배" → "롯데" → "롯데글로벌로지스" 매칭 가능하도록 처리
        def _normalize(name: str) -> str:
            return (
                (name or "")
                .replace("택배", "")
                .replace("(주)", "")
                .replace(" ", "")
                .strip()
                .lower()
            )

        carriers = await self.get_carriers()
        input_norm = _normalize(shipping_company_name)

        def _name_of(c: dict) -> str:
            # 카페24 응답 필드명 호환 (버전별로 다름)
            return c.get("shipping_carrier") or c.get("carrier_name") or ""

        def _code_of(c: dict) -> str:
            return c.get("shipping_carrier_code") or c.get("carrier_code") or ""

        carrier_code = None
        if input_norm:
            for c in carriers:
                name_norm = _normalize(_name_of(c))
                if not name_norm:
                    continue
                # 양방향 부분일치
                if input_norm in name_norm or name_norm in input_norm:
                    carrier_code = _code_of(c)
                    break

        if not carrier_code:
            available = [_name_of(c) for c in carriers]
            raise Cafe24ApiError(
                f"택배사 매칭 실패: '{shipping_company_name}' (사용가능: {available})"
            )

        body = {
            "request": {
                "shop_no": 1,
                "tracking_no": tracking_no,
                "shipping_company_code": carrier_code,
                "status": "shipping",
            }
        }
        return await self._call_api(
            "POST",
            f"/orders/{order_id}/shipments",
            body=body,
        )

    async def get_return_claim_code(self, order_id: str) -> str | None:
        """주문에 연결된 반품 claim_code 조회.

        GET /orders/{order_id}?embed=return → order.return[] 배열에서 진행중 반품 우선 선택.
        """
        try:
            data = await self._call_api(
                "GET",
                f"/orders/{order_id}",
                params={"shop_no": 1, "embed": "return"},
            )
        except Exception as e:
            raise Cafe24ApiError(f"주문 조회 실패: {e}")

        order = (data or {}).get("order") or {}
        # embed=return 응답 구조 호환: order.return / order.returns / order.return_info
        returns = (
            order.get("return")
            or order.get("returns")
            or order.get("return_info")
            or []
        )
        if isinstance(returns, dict):
            returns = [returns]
        if not returns:
            logger.warning(
                f"[카페24-반품] order_id={order_id} embed 응답에 반품 정보 없음: keys={list(order.keys())}"
            )
            return None

        # 진행중(returned가 아닌) 반품 우선
        pending = [r for r in returns if (r.get("status") or "") != "returned"]
        target = pending[0] if pending else returns[0]
        return target.get("claim_code") or target.get("return_code")

    async def approve_return(self, order_id: str) -> dict[str, Any]:
        """반품 승인 처리 (수거완료 + 반품완료 + 재고복구).

        PUT /orders/{order_id}/return/{claim_code}
        body: status=returned, pickup_completed=T, recover_inventory=T
        """
        claim_code = await self.get_return_claim_code(order_id)
        if not claim_code:
            raise Cafe24ApiError(
                f"반품 claim_code를 찾을 수 없습니다 (order_id={order_id})"
            )

        body = {
            "request": {
                "shop_no": 1,
                "status": "returned",
                "pickup_completed": "T",
                "recover_inventory": "T",
            }
        }
        return await self._call_api(
            "PUT",
            f"/orders/{order_id}/return/{claim_code}",
            body=body,
        )

    async def reject_return_request(
        self, order_id: str, reason: str = "판매자 거부"
    ) -> str:
        """반품요청 접수 거부 (반품요청 단계, claim_code 없음).

        PUT /admin/returnrequests with undone=T
        → 카페24가 자동으로 이전 상태(배송중/배송완료 등)로 복귀시킴.

        Returns: 거부 후 카페24의 한글 상태 텍스트 (e.g., '배송완료')
        """
        # 1. 주문 항목 조회 → order_item_code 추출 (전용 엔드포인트)
        try:
            items_data = await self._call_api(
                "GET",
                f"/orders/{order_id}/items",
                params={"shop_no": 1},
            )
        except Exception as e:
            raise Cafe24ApiError(f"주문 항목 조회 실패: {e}")

        raw_items = (items_data or {}).get("items") or []
        if not raw_items:
            raise Cafe24ApiError(f"주문 item을 찾을 수 없습니다 (order_id={order_id})")

        item_codes = [
            it.get("order_item_code") for it in raw_items if it.get("order_item_code")
        ]
        if not item_codes:
            raise Cafe24ApiError("order_item_code를 찾을 수 없습니다")

        logger.info(f"[카페24-반품거부] order_id={order_id} item_codes={item_codes}")

        # 2. PUT /returnrequests — requests 배열 wrapper 사용 (이 endpoint 전용)
        body = {
            "shop_no": 1,
            "requests": [
                {
                    "order_id": order_id,
                    "order_item_code": item_codes,
                    "undone": "T",
                    "reason_type": "I",  # 기타
                    "reason": reason,
                }
            ],
        }
        logger.info(f"[카페24-반품거부] PUT /returnrequests body={body}")
        await self._call_api("PUT", "/returnrequests", body=body)

        # 3. 거부 후 카페24의 실제 복귀 상태를 재조회
        try:
            refreshed = await self._call_api(
                "GET",
                f"/orders/{order_id}",
                params={"shop_no": 1, "embed": "items"},
            )
            new_items = ((refreshed or {}).get("order") or {}).get("items") or []
            if new_items:
                return (new_items[0].get("status_text") or "").strip() or "배송완료"
        except Exception as e:
            logger.warning(f"[카페24-반품] 거부 후 상태 재조회 실패: {e}")
        return "배송완료"

    async def reply_inquiry(self, inquiry_no: int, reply: str) -> dict[str, Any]:
        """상품문의 답변 등록 (일반 inquiries API — 일반 앱 미지원).

        대부분 카페24 일반 앱은 mall.read_inquiry 권한이 없어 동작하지 않음.
        실제로는 reply_board_article()을 사용해야 함.
        """
        data = await self._call_api(
            "POST",
            f"/inquiries/{inquiry_no}/replies",
            body={"request": {"shop_no": 1, "content": reply}},
        )
        return data

    async def reply_board_article(
        self, board_no: int, article_no: int, reply: str
    ) -> dict[str, Any]:
        """게시판 article 답변 등록 (댓글 생성 방식).

        카페24 상품 Q&A 답변은 article의 reply 필드가 아닌 댓글(comments)로 처리됨.
        POST /boards/{board_no}/articles/{article_no}/comments
        스코프: mall.write_community
        """
        body = {
            "shop_no": 1,
            "request": {
                "content": reply,
                "writer": "관리자",
                "writer_type": "A",
                "password": "samba1234!",  # 카페24 댓글 필수 필드 (관리자 댓글용 더미)
            },
        }
        logger.info(
            f"[카페24-CS답변] POST /boards/{board_no}/articles/{article_no}/comments body={body}"
        )
        try:
            resp = await self._call_api(
                "POST",
                f"/boards/{board_no}/articles/{article_no}/comments",
                body=body,
            )
            logger.info(f"[카페24-CS답변] 응답: {resp}")
            return resp
        except Exception as e:
            logger.error(f"[카페24-CS답변] 댓글 생성 실패: {e}")
            raise

    # ── 이미지 업로드 ──────────────────────────────

    async def _upload_image_bytes_to_products_api(
        self, image_bytes: bytes
    ) -> str | None:
        """이미지 바이트를 Base64로 POST /products/images에 업로드 → 카페24 서버 경로 반환.

        SCOPE: mall.write_product (별도 파일 스코프 불필요)
        """
        import base64

        encoded = base64.b64encode(image_bytes).decode()
        try:
            # 카페24 Products Images API는 request 래퍼 + 배열 형식 필요
            result = await self._call_api(
                "POST",
                "/products/images",
                body={"shop_no": 1, "request": [{"image": encoded}]},
            )
            images = result.get("images") or []
            if images:
                path = images[0].get("path") or images[0].get("detail_image")
            else:
                img = result.get("image") or result
                path = img.get("path") if isinstance(img, dict) else None
            if path:
                # 전체 URL → 상대경로 추출 (예: /web/upload/NNEditor/...)
                path_str = str(path)
                if path_str.startswith("https://") and f"/{self.mall_id}/" in path_str:
                    path_str = "/" + path_str.split(f"/{self.mall_id}/", 1)[1]
                logger.info(f"[카페24] Products Images API 업로드 성공: {path_str}")
                return path_str
            logger.warning(f"[카페24] Products Images API 응답에 path 없음: {result}")
        except Exception as e:
            logger.warning(f"[카페24] Products Images API 실패: {e}")
        return None

    async def upload_images(
        self,
        product_no: int,
        image_urls: list[str],
    ) -> dict[str, Any]:
        """이미지를 카페24 Products Images API(mall.write_product 스코프)로 업로드 후 상품에 등록.

        흐름:
        1. 백엔드에서 원본 이미지 다운로드 (CDN 핫링크 차단 우회)
        2. POST /products/images (Base64) → 카페24 서버 경로 취득
        3. POST /products/{no}/images → 상품 대표이미지 등록 (image_upload_type:A 자동리사이징)
        """
        import base64

        main_bytes: bytes | None = None
        extra_bytes: list[bytes] = []  # 추가이미지 bytes 보관

        # 대표이미지 1장 + 추가이미지 최대 5장 — 병렬 다운로드
        urls = []
        for url in image_urls[:6]:
            if url.startswith("//"):
                url = "https:" + url
            if url.startswith("http"):
                urls.append(url)

        results = await asyncio.gather(
            *[self._download_image(u) for u in urls], return_exceptions=True
        )
        for i, res in enumerate(results):
            if isinstance(res, Exception) or not res or not res[0]:
                logger.warning(f"[카페24] 이미지 다운로드 실패 [{i}]")
                continue
            image_bytes = res[0]
            if main_bytes is None:
                main_bytes = image_bytes
            else:
                extra_bytes.append(image_bytes)

        if not main_bytes:
            logger.warning("[카페24] 이미지 다운로드 전체 실패 — 이미지 설정 생략")
            return {}

        # 2단계: POST /products/{no}/images — Base64 직접 전달 (NNEditor 경로 우회)
        result = await self._call_api(
            "POST",
            f"/products/{product_no}/images",
            body={
                "shop_no": 1,
                "request": {
                    "product_no": product_no,
                    "detail_image": base64.b64encode(main_bytes).decode(),
                    "image_upload_type": "A",
                },
            },
        )
        logger.info(f"[카페24] 대표이미지 등록 완료: product_no={product_no}")

        # 추가 이미지: POST /products/{no}/additionalimages — Base64 배열 형식
        if extra_bytes:
            try:
                await self._call_api(
                    "POST",
                    f"/products/{product_no}/additionalimages",
                    body={
                        "shop_no": 1,
                        "request": {
                            "additional_image": [
                                base64.b64encode(b).decode() for b in extra_bytes[:20]
                            ],
                        },
                    },
                )
                logger.info(f"[카페24] 추가이미지 {len(extra_bytes)}개 등록 완료")
            except Exception as e:
                logger.warning(f"[카페24] 추가이미지 등록 실패 (무시): {e}")

        return result

    async def _download_image(self, url: str) -> tuple[bytes | None, str]:
        """브라우저 헤더로 이미지 다운로드 (CDN 핫링크 차단 우회)."""
        from urllib.parse import urlparse

        _parsed = urlparse(url)
        _host = _parsed.netloc or ""
        # 소싱처별 Referer — 핫링크 차단 회피
        if "msscdn.net" in _host:
            referer = "https://www.musinsa.com/"
        elif "kream" in _host:
            referer = "https://kream.co.kr/"
        elif "fashionplus" in _host:
            referer = "https://www.fashionplus.co.kr/"
        elif "nike.com" in _host:
            referer = "https://www.nike.com/"
        elif "a-rt.com" in _host:
            referer = "https://www.a-rt.com/"
        elif "ssgcdn.com" in _host:
            referer = "https://www.ssg.com/"
        elif "lotteimall.com" in _host:
            referer = "https://www.lotteon.com/"
        elif "gsshop" in _host:
            referer = "https://www.gsshop.com/"
        elif "pstatic.net" in _host:
            referer = "https://smartstore.naver.com/"
        else:
            referer = f"{_parsed.scheme}://{_host}/"
        headers = {
            "Referer": referer,
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            # 카페24는 WebP 미지원 → JPEG/PNG만 요청
            "Accept": "image/jpeg,image/png,image/gif,image/*;q=0.8,*/*;q=0.5",
        }
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    content_type = (
                        resp.headers.get("content-type", "image/jpeg")
                        .split(";")[0]
                        .strip()
                    )
                    image_bytes = resp.content
                    # WebP/AVIF → JPEG 변환 (카페24 WebP/AVIF 미지원)
                    if content_type in ("image/webp", "image/avif"):
                        image_bytes = await asyncio.to_thread(
                            _convert_to_jpeg, image_bytes
                        )
                        content_type = "image/jpeg"
                        logger.info(f"[카페24] WebP→JPEG 변환: {len(image_bytes)}bytes")
                    logger.info(
                        f"[카페24] 이미지 다운로드 성공: {len(image_bytes)}bytes, {content_type}"
                    )
                    return image_bytes, content_type
                logger.warning(
                    f"[카페24] 이미지 다운로드 실패: HTTP {resp.status_code} {url[:80]}"
                )
        except Exception as e:
            logger.warning(f"[카페24] 이미지 다운로드 예외: {e}")
        return None, "image/jpeg"

    async def _upload_to_s3(
        self,
        image_bytes: bytes,
        content_type: str,
        original_url: str,
        index: int,
    ) -> str | None:
        """이미지 바이트를 S3에 업로드하고 공개 URL 반환. S3 미설정 시 None."""
        import asyncio
        import hashlib

        try:
            from backend.core.config import settings

            bucket = getattr(settings, "s3_bucket_name", "") or ""
            region = getattr(settings, "aws_region", "") or ""
            key_id = getattr(settings, "aws_access_key_id", "") or ""
            if not bucket or not key_id:
                logger.debug("[카페24] S3 미설정 — 이미지 S3 업로드 생략")
                return None

            # 확장자 결정
            ext_map = {
                "image/jpeg": "jpg",
                "image/jpg": "jpg",
                "image/png": "png",
                "image/webp": "webp",
                "image/gif": "gif",
            }
            ext = ext_map.get(content_type, "jpg")
            # [2026-05-24] content_hash 기반 결정적 키 + HeadObject 가드
            # 기존: uuid.uuid4() 키 → 같은 이미지 매번 새 객체 PUT → S3 egress 누수
            content_hash = hashlib.md5(image_bytes).hexdigest()[:16]
            s3_key = f"samba/cafe24-images/{content_hash}.{ext}"
            s3_url = f"https://{bucket}.s3.{region}.amazonaws.com/{s3_key}"

            # 동기 boto3를 스레드풀에서 실행 (이벤트 루프 블로킹 방지)
            def _do_upload() -> str:
                from backend.utils.s3 import get_s3_client

                client = get_s3_client()
                # 동일 content_hash 객체 이미 존재 시 PUT 스킵
                try:
                    client.head_object(Bucket=bucket, Key=s3_key)
                    return s3_url
                except Exception:
                    pass
                client.put_object(
                    Bucket=bucket,
                    Key=s3_key,
                    Body=image_bytes,
                    ContentType=content_type,
                )
                return s3_url

            s3_url = await asyncio.to_thread(_do_upload)
            return s3_url

        except Exception as e:
            logger.warning(f"[카페24] S3 put_object 실패: {e}")
            return None

    # ── 옵션 / Variants ────────────────────────────

    async def register_options(
        self,
        product_no: int,
        options: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """상품 옵션 등록.

        options: [{"name": "색상", "values": ["빨강", "파랑"]}, ...]
        """
        option_list = []
        for opt in options:
            option_list.append(
                {
                    "option_name": opt["name"],
                    "option_value": [{"option_value_name": v} for v in opt["values"]],
                }
            )
        return await self._call_api(
            "POST",
            f"/products/{product_no}/options",
            body={"shop_no": 1, "request": {"options": option_list}},
        )

    async def get_or_create_manufacturer(self, name: str) -> str | None:
        """제조사명 → 카페24 manufacturer_code (없으면 자동 생성).

        카페24 제약: manufacturer_name / president_name 모두 30자 미만 필수.
        수집 원본이 "제조사: Nike inc. / 수입처 : 나이키코리아(유)" 형태이므로
        접두사/구분자 제거 후 30자 이내로 정제한다.
        """
        if not name or not name.strip():
            logger.info("[카페24][제조사API] 빈 이름 → None 반환")
            return None
        # 정제: "제조사:" 접두사 제거 → "/" 또는 "수입처" 앞부분만 → 30자 제한
        import re as _re

        cleaned = _re.sub(r"^\s*제조사\s*[:：]\s*", "", name.strip())
        cleaned = cleaned.split("/")[0]
        cleaned = cleaned.split("수입처")[0]
        cleaned = cleaned.strip(" :：-,.")
        if len(cleaned) >= 30:
            cleaned = cleaned[:29].strip()
        if not cleaned:
            logger.info(f"[카페24][제조사API] 정제 후 빈 값 → None (원본={name!r})")
            return None
        if cleaned != name.strip():
            logger.info(f"[카페24][제조사API] 정제: {name!r} → {cleaned!r}")
        name = cleaned
        logger.info(f"[카페24][제조사API] 요청명={name!r} (len={len(name)})")
        try:
            data = await self._call_api("GET", "/manufacturers", params={"limit": 100})
            _list = data.get("manufacturers", [])
            for m in _list:
                if (m.get("manufacturer_name") or "").strip() == name:
                    _code = m.get("manufacturer_code")
                    logger.info(
                        f"[카페24][제조사API] 기존 일치 발견: {name!r} → {_code}"
                    )
                    return _code
            logger.info(
                f"[카페24][제조사API] 조회 {len(_list)}건, 일치없음 → 생성시도 {name!r}"
            )
            # 없으면 생성 (president_name 필수 필드 — 브랜드명으로 대체)
            resp = await self._call_api(
                "POST",
                "/manufacturers",
                body={"request": {"manufacturer_name": name, "president_name": name}},
            )
            code = (resp.get("manufacturer") or {}).get("manufacturer_code")
            logger.info(f"[카페24][제조사API] 자동 생성 성공: {name!r} → {code}")
            return code
        except Exception as e:
            logger.warning(
                f"[카페24][제조사API] 실패: name={name!r} err={e}", exc_info=True
            )
            return None

    async def get_or_create_brand(self, name: str) -> str | None:
        """브랜드명 → 카페24 brand_code (없으면 자동 생성)."""
        if not name or not name.strip():
            return None
        name = name.strip()
        try:
            data = await self._call_api("GET", "/brands", params={"limit": 100})
            for b in data.get("brands", []):
                if (b.get("brand_name") or "").strip() == name:
                    return b.get("brand_code")
            # 없으면 생성
            resp = await self._call_api(
                "POST",
                "/brands",
                body={"request": {"brand_name": name}},
            )
            code = (resp.get("brand") or {}).get("brand_code")
            logger.info(f"[카페24] 브랜드 자동 생성: {name} → {code}")
            return code
        except Exception as e:
            logger.warning(f"[카페24] 브랜드 조회/생성 실패: {e}")
            return None

    async def get_variants(self, product_no: int) -> list[dict[str, Any]]:
        """품목(variant) 목록 조회."""
        data = await self._call_api("GET", f"/products/{product_no}/variants")
        return data.get("variants", [])

    async def update_variant(
        self,
        product_no: int,
        variant_code: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """품목별 가격/재고 수정."""
        return await self._call_api(
            "PUT",
            f"/products/{product_no}/variants/{variant_code}",
            body={"shop_no": 1, "request": payload},
        )

    # ── 판매중지/재개 ──────────────────────────────

    async def has_products_in_category(
        self, category_no: int, shop_no: int = 1
    ) -> bool:
        """카테고리에 상품이 있는지 확인 — GET /products?category 사용."""
        try:
            data = await self._call_api(
                "GET",
                "/products",
                params={"shop_no": shop_no, "category": category_no, "limit": 1},
            )
            return len(data.get("products", [])) > 0
        except Exception as e:
            logger.warning(
                f"[카페24] 카테고리 {category_no} 상품 확인 실패 (삭제 스킵): {e}"
            )
            return True  # 확인 실패 시 안전하게 삭제 안 함

    async def try_delete_category_if_empty(
        self, category_no: int, shop_no: int = 1
    ) -> bool:
        """상품 없는 경우에만 카테고리 삭제."""
        if await self.has_products_in_category(category_no, shop_no):
            logger.debug(f"[카페24] 카테고리 {category_no} 상품 있음 → 삭제 스킵")
            return False
        try:
            await self._call_api("DELETE", f"/categories/{category_no}")
            logger.info(f"[카페24] 빈 카테고리 삭제: no={category_no}")
            return True
        except Exception as e:
            logger.warning(f"[카페24] 카테고리 {category_no} 삭제 실패: {e}")
            return False

    async def delete_category(self, category_no: int) -> bool:
        """카테고리 삭제."""
        try:
            await self._call_api("DELETE", f"/categories/{category_no}")
            logger.info(f"[카페24] 빈 카테고리 삭제: no={category_no}")
            return True
        except Exception as e:
            logger.warning(f"[카페24] 카테고리 삭제 실패 no={category_no}: {e}")
            return False

    async def cleanup_empty_categories(self, shop_no: int = 1) -> None:
        """상품이 없는 빈 카테고리를 리프부터 상위 순으로 삭제.

        전략: 리프(자식 없는) 카테고리만 상품 수 확인 → 빈 경우 삭제 → 반복
        카테고리 수 API 호출 최소화를 위해 리프만 대상으로 함.
        """
        all_cats = await self.get_categories(shop_no)
        if not all_cats:
            return

        # category_no → parent_no 맵, parent_no → children set 맵
        parent_map: dict[int, int] = {}
        children_map: dict[int, set[int]] = {}
        for c in all_cats:
            cno = int(c.get("category_no", 0))
            pno = int(c.get("parent_category_no") or 0)
            if pno == 1:
                pno = 0
            parent_map[cno] = pno
            children_map.setdefault(pno, set()).add(cno)
            children_map.setdefault(cno, set())

        deleted_count = 0
        # 리프 카테고리(자식 없는) 목록 추출
        leaves = [
            cno for cno, children in children_map.items() if cno != 0 and not children
        ]

        while leaves:
            # 리프 카테고리 병렬 삭제 시도 (빈 경우만 삭제됨)
            results = await asyncio.gather(
                *[self.try_delete_category_if_empty(cno, shop_no) for cno in leaves],
                return_exceptions=True,
            )
            next_leaves = []
            for cno, deleted in zip(leaves, results):
                if deleted is True:
                    deleted_count += 1
                    pno = parent_map.get(cno, 0)
                    if pno and pno in children_map:
                        children_map[pno].discard(cno)
                        if not children_map[pno]:
                            next_leaves.append(pno)
            leaves = next_leaves

        logger.info(f"[카페24] 빈 카테고리 정리 완료: {deleted_count}개 삭제")

    async def stop_selling(self, product_no: int) -> dict[str, Any]:
        """판매중지 (display=F, selling=F)."""
        return await self.update_product(
            product_no,
            {
                "shop_no": 1,
                "request": {"display": "F", "selling": "F"},
            },
        )

    async def resume_selling(self, product_no: int) -> dict[str, Any]:
        """판매재개 (display=T, selling=T)."""
        return await self.update_product(
            product_no,
            {
                "shop_no": 1,
                "request": {"display": "T", "selling": "T"},
            },
        )

    # ── 상품 데이터 변환 ──────────────────────────

    @staticmethod
    def transform_product(
        product: dict[str, Any],
        category_id: str | int = "",
    ) -> dict[str, Any]:
        """SambaCollectedProduct → 카페24 상품 등록 포맷 변환."""

        # 가격 계산
        sale_price = int(product.get("sale_price", 0) or 0)
        original_price = int(product.get("original_price", 0) or 0)
        if sale_price <= 0:
            sale_price = original_price or 10000
        # 100원 단위 내림
        sale_price = (sale_price // 100) * 100

        # 정가(비교가): 원래가격이 판매가보다 높으면 표시
        retail_price = original_price if original_price > sale_price else 0
        if retail_price:
            retail_price = (retail_price // 100) * 100

        # 상품명 (최대 250자)
        name = (product.get("name") or "상품명 없음")[:250]

        # 상세설명 HTML
        detail_html = product.get("detail_html", "") or ""
        # 프로토콜 없는 이미지 URL 보정
        if detail_html:
            detail_html = re.sub(r'(src=["\'])\/\/', r"\1https://", detail_html)
        # 상품정보제공고시 테이블 하단 추가
        detail_html += _build_notice_html(product)

        # 요약 설명 (255자)
        summary = ""
        brand = product.get("brand", "")
        if brand:
            summary = f"[{brand}] {name}"[:255]

        # 이미지는 Files API 업로드 후 PUT /products로 별도 설정
        # (카페24 POST /products는 자체 서버 경로만 허용 — 외부 URL 불가)
        image_fields: dict[str, Any] = {}

        # 옵션 처리
        options = product.get("options") or []
        has_option = "T" if options else "F"

        # 재고: 옵션 합산 또는 정책 제한
        max_stock = product.get("_max_stock", 0)
        if options:
            total_stock = sum(
                (o.get("stock") or 0) for o in options if not o.get("isSoldOut")
            )
        else:
            total_stock = 999
        if max_stock and max_stock > 0:
            total_stock = min(max_stock, total_stock) if total_stock > 0 else max_stock
        if total_stock <= 0:
            total_stock = 0

        # 배송비
        delivery_fee_type = product.get("_delivery_fee_type", "")
        delivery_base_fee = int(product.get("_delivery_base_fee", 0) or 0)
        # 카페24: shipping_fee_type — T(무료), R(고정배송비), M(구매금액기준)
        if delivery_fee_type == "PAID" and delivery_base_fee > 0:
            shipping_type = "R"  # 고정배송비
            shipping_fee = delivery_base_fee
        else:
            shipping_type = "T"  # 무료
            shipping_fee = 0

        # 상품 상태
        product_condition = "N"  # 신상품

        # 사용자 정의 코드 (소싱처 상품코드)
        custom_code = (product.get("source_product_id") or product.get("id", ""))[:40]

        request_body: dict[str, Any] = {
            "product_name": name,
            "display": "T",
            "selling": "T",
            "product_condition": product_condition,
            "price": sale_price,
            "supply_price": sale_price,
            "has_option": has_option,
            "description": detail_html,
            "custom_product_code": custom_code,
            "summary_description": summary,
            "shipping_fee_by_product": "T",  # 개별배송비 사용
            "shipping_method": "01",  # 택배
            "shipping_fee_type": shipping_type,
            **image_fields,
        }

        if retail_price:
            request_body["retail_price"] = retail_price
        # 고정배송비(R)는 shipping_rates 배열로 전달 (shipping_fee 필드 없음)
        if shipping_fee and shipping_type == "R":
            request_body["shipping_rates"] = [{"shipping_fee": shipping_fee}]

        # 카테고리 — 상품 등록 시 함께 설정 (별도 API 불필요)
        if category_id:
            try:
                request_body["add_category_no"] = [{"category_no": int(category_id)}]
            except (ValueError, TypeError):
                pass

        # 제조사/브랜드 코드
        manufacturer_code = product.get("_manufacturer_code")
        brand_code = product.get("_brand_code")
        if manufacturer_code:
            request_body["manufacturer_code"] = manufacturer_code
        if brand_code:
            request_body["brand_code"] = brand_code

        # 원산지: POST /products는 origin 무시 → 생성 후 별도 PUT으로 설정 (plugin에서 처리)

        # 상품 소재
        material = product.get("material") or ""
        if material:
            request_body["product_material"] = material

        # 모델명 (품번/style_code)
        style_code = product.get("style_code") or ""
        if style_code:
            request_body["model_name"] = style_code[:100]

        # 검색어 태그 (SEO meta_keywords와 동일 소스: brand + tags)
        tag_list: list[str] = []
        brand_name = (product.get("brand") or "").strip()
        if brand_name:
            tag_list.append(brand_name)
        raw_tags = product.get("tags") or []
        product_name_lower = name.lower()
        brand_lower = brand_name.lower()
        for t in raw_tags:
            t_str = str(t).strip()
            if not t_str:
                continue
            # 내부 마커 태그 제외 (예: __ai_tagged__)
            if t_str.startswith("__") and t_str.endswith("__"):
                continue
            t_lower = t_str.lower()
            if t_lower == brand_lower or t_lower in product_name_lower:
                continue
            if t_str not in tag_list:
                tag_list.append(t_str)
            if len(tag_list) >= 20:
                break
        if tag_list:
            request_body["product_tag"] = tag_list

        # 배송기간 (minimum/maximum 키 사용)
        shipping_days = product.get("_shipping_days", 3)
        request_body["shipping_period"] = {
            "minimum": shipping_days,
            "maximum": shipping_days,
        }

        return {"shop_no": 1, "request": request_body}

    @staticmethod
    def build_options_payload(
        options: list[dict[str, Any]],
        sale_price: int,
        max_stock_per_option: int = 0,
    ) -> list[dict[str, Any]] | None:
        """수집 옵션 → 카페24 옵션 등록 포맷 변환.

        카페24 옵션 구조:
        - 옵션명/값 등록 → variants 자동 생성 → variant별 가격/재고 설정
        """
        if not options:
            return None

        # 옵션명에 "/" 포함 → 2단 옵션 (색상/사이즈)
        has_slash = any("/" in (o.get("name") or "") for o in options)

        if has_slash:
            # 2단 옵션: 색상, 사이즈 분리
            colors: list[str] = []
            sizes: list[str] = []
            for o in options:
                name = o.get("name") or o.get("size") or ""
                if "/" in name:
                    # 마지막 "/" 기준으로 분리 — L/BEIGE/230 → 색상="L/BEIGE", 사이즈="230"
                    parts = [p.strip() for p in name.rsplit("/", 1)]
                    if parts[0] and parts[0] not in colors:
                        colors.append(parts[0])
                    if len(parts) > 1 and parts[1] and parts[1] not in sizes:
                        sizes.append(parts[1])

            result = []
            if colors:
                result.append({"name": "색상", "values": colors})
            if sizes:
                result.append({"name": "사이즈", "values": sizes})
            return result if result else None
        else:
            # 1단 옵션: 사이즈
            values = []
            for o in options:
                name = o.get("name") or o.get("size") or ""
                if name and name not in values:
                    values.append(name)
            return [{"name": "사이즈", "values": values}] if values else None

    @staticmethod
    def build_variant_updates(
        options: list[dict[str, Any]],
        variants: list[dict[str, Any]],
        sale_price: int,
        max_stock: int = 0,
    ) -> list[dict[str, Any]]:
        """수집 옵션 데이터 → 카페24 variant별 가격/재고 업데이트 목록.

        variants: get_variants() 결과
        반환: [{"variant_code": "...", "quantity": N, "price": N}, ...]
        """
        # 옵션명 → 재고/가격 매핑
        opt_map: dict[str, dict[str, Any]] = {}
        for o in options:
            name = (o.get("name") or o.get("size") or "").strip()
            if name:
                data = {
                    "stock": o.get("stock", 0) or 0,
                    "sold_out": o.get("isSoldOut", False),
                    "price": int(o.get("price", 0) or 0),
                }
                opt_map[name] = data
                # variant 매칭 키는 "색상 / 사이즈" 형태 → 정규화 키도 등록
                # 예: "BEIGE/230" → "BEIGE / 230", "L/BEIGE/230" → "L/BEIGE / 230"
                if "/" in name:
                    parts = name.rsplit("/", 1)
                    normalized = f"{parts[0].strip()} / {parts[1].strip()}"
                    opt_map[normalized] = data

        updates = []
        for v in variants:
            vcode = v.get("variant_code", "")
            # variant의 옵션값 조합으로 매칭
            # cafe24 variants API: options=[{"name":"색상","value":"BEIGE"},{"name":"사이즈","value":"230"}]
            option_values = [
                opt.get("value", "")
                for opt in (v.get("options") or [])
                if opt.get("value")
            ]
            option_key = (
                " / ".join(option_values)
                if len(option_values) > 1
                else (option_values[0] if option_values else "")
            )

            matched = opt_map.get(option_key)
            if matched:
                stock = 0 if matched["sold_out"] else matched["stock"]
                if max_stock and max_stock > 0:
                    stock = min(stock, max_stock)
                logger.info(
                    f"[카페24][재고매칭] ✓ key={option_key!r} "
                    f"soldOut={matched['sold_out']} stock={matched['stock']} → qty={max(stock, 0)}"
                )
                updates.append(
                    {
                        "variant_code": vcode,
                        "use_inventory": "T",
                        "display_soldout": "T",
                        "quantity": max(stock, 0),
                    }
                )
            else:
                # 매칭 안 되면 기본 재고 설정
                default_stock = max_stock if max_stock > 0 else 10
                logger.warning(
                    f"[카페24][재고매칭] ✗ 매칭실패 key={option_key!r} → 기본재고={default_stock}"
                )
                updates.append(
                    {
                        "variant_code": vcode,
                        "use_inventory": "T",
                        "display_soldout": "T",
                        "quantity": default_stock,
                    }
                )

        return updates
