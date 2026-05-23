"""ABC마트 소싱처 플러그인.

ABC마트와 그랜드스테이지는 동일 도메인(www.a-rt.com)의 동일 구조 사이트이므로,
ABC마트 검색 시 그랜드스테이지 상품도 함께 수집하여 반환한다.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from backend.domain.samba.plugins.sourcing_base import SourcingPlugin

if TYPE_CHECKING:
    from backend.domain.samba.collector.refresher import RefreshResult

logger = logging.getLogger(__name__)


class AbcMartPlugin(SourcingPlugin):
    """ABC마트 소싱처 플러그인.

    search() 호출 시 ABC마트 + 그랜드스테이지 양쪽을 병렬 검색하여
    결과를 병합해 반환한다 (교차 수집).

    concurrency=3: 두 사이트 동시 요청을 고려한 세마포어
    request_interval=0.5: 요청 간 500ms 딜레이
    """

    site_name = "ABCmart"
    concurrency = 3
    request_interval = 0.5

    async def search(self, keyword: str, **filters) -> list[dict]:
        """ABC마트 + 그랜드스테이지 병렬 검색 후 결과 병합.

        두 사이트의 결과를 합쳐 중복(siteProductId 기준)을 제거한 뒤 반환한다.
        """
        from backend.domain.samba.proxy.abcmart import ARTSourcingClient

        abc_client = ARTSourcingClient(channel=None)  # ABC마트
        gs_client = ARTSourcingClient(channel="10002")  # 그랜드스테이지

        page = filters.get("page", 1)
        size = filters.get("size", 40)

        abc_results, gs_results = await asyncio.gather(
            self.safe_call(abc_client.search_products(keyword, page=page, size=size)),
            self.safe_call(gs_client.search_products(keyword, page=page, size=size)),
            return_exceptions=True,
        )

        # 예외 발생 시 빈 리스트로 대체
        if isinstance(abc_results, Exception):
            logger.warning(f"[ABCmart] 검색 실패 (ABC마트): {abc_results}")
            abc_results = []
        if isinstance(gs_results, Exception):
            logger.warning(f"[ABCmart] 검색 실패 (그랜드스테이지): {gs_results}")
            gs_results = []

        # 중복 제거 (ABC마트 우선, 동일 prdtNo는 첫 번째 것만 유지)
        seen: set[str] = set()
        merged: list[dict] = []
        for item in list(abc_results) + list(gs_results):
            pid = item.get("siteProductId") or item.get("goodsNo", "")
            if pid and pid in seen:
                continue
            if pid:
                seen.add(pid)
            merged.append(item)

        logger.info(
            f"[ABCmart] 검색 병합 완료: '{keyword}' "
            f"ABC마트 {len(abc_results)}개 + 그랜드스테이지 {len(gs_results)}개 "
            f"→ 총 {len(merged)}개"
        )
        return merged

    async def scan_categories(self, keyword: str) -> dict:
        """ABC마트 + 그랜드스테이지 카테고리 스캔 후 병합."""
        from backend.domain.samba.proxy.abcmart import ARTSourcingClient

        abc_client = ARTSourcingClient(channel=None)  # ABC마트
        gs_client = ARTSourcingClient(channel="10002")  # 그랜드스테이지

        abc_result, gs_result = await asyncio.gather(
            self.safe_call(abc_client.scan_categories(keyword)),
            self.safe_call(gs_client.scan_categories(keyword)),
            return_exceptions=True,
        )

        if isinstance(abc_result, Exception):
            logger.warning(f"[ABCmart] 카테고리 스캔 실패 (ABC마트): {abc_result}")
            abc_result = {"categories": [], "total": 0, "groupCount": 0}
        if isinstance(gs_result, Exception):
            logger.warning(
                f"[ABCmart] 카테고리 스캔 실패 (그랜드스테이지): {gs_result}"
            )
            gs_result = {"categories": [], "total": 0, "groupCount": 0}

        # 카테고리 병합 (같은 path는 count 합산)
        merged: dict[str, dict] = {}
        for cat in abc_result.get("categories", []) + gs_result.get("categories", []):
            path = cat.get("path", "")
            if path in merged:
                merged[path]["count"] += cat.get("count", 0)
            else:
                merged[path] = {**cat}

        categories = sorted(merged.values(), key=lambda x: -x.get("count", 0))
        total = sum(c.get("count", 0) for c in categories)

        logger.info(
            f"[ABCmart] 카테고리 스캔 병합 완료: '{keyword}' "
            f"→ {len(categories)}개 카테고리, 총 {total}건"
        )
        return {
            "categories": list(categories),
            "total": total,
            "groupCount": len(categories),
        }

    async def get_detail(self, site_product_id: str) -> dict:
        """ABC마트 상품 상세 조회."""
        from backend.domain.samba.proxy.abcmart import ARTSourcingClient

        client = ARTSourcingClient(channel=None)
        return await self.safe_call(client.get_product_detail(site_product_id))

    async def refresh(self, product) -> "RefreshResult":
        """가격/재고/상세 갱신 — ARTSourcingClient 직접 API 호출."""
        from backend.domain.samba.collector.refresher import RefreshResult
        from backend.domain.samba.proxy.abcmart import ARTSourcingClient

        product_id = getattr(product, "id", "")
        site_product_id = getattr(product, "site_product_id", "") or getattr(
            product, "siteProductId", ""
        )

        if not site_product_id:
            return RefreshResult(
                product_id=product_id,
                error="ABCmart 상품 ID 없음",
            )

        try:
            from backend.domain.samba.collector.refresher import (
                _current_refresh_source,
                _get_rotated_proxy,
            )

            # 오토튠 실행 시 무신사와 동일하게 20건마다 프록시 로테이션
            _proxy = (
                _get_rotated_proxy(site="ABCmart")
                if _current_refresh_source.get() == "autotune"
                else None
            )

            # 채널 결정: source_url에 tChnnlNo=10002가 있으면 GrandStage 우선
            # (ABCmart/GrandStage는 동일 도메인이지만 채널별로 가격/혜택가가 다를 수 있음)
            _src_url = (getattr(product, "source_url", "") or "").lower()
            _prefer_grandstage = (
                "tchnnlno=10002" in _src_url or "grandstage" in _src_url
            )

            async def _fetch(channel: str | None) -> dict:
                _client = ARTSourcingClient(
                    channel=channel, proxy_pool=[_proxy] if _proxy else None
                )
                return await self.safe_call(
                    _client.get_product_detail(site_product_id, refresh_only=True)
                )

            primary_channel = "10002" if _prefer_grandstage else None
            fallback_channel = None if _prefer_grandstage else "10002"

            detail = await _fetch(primary_channel)

            # 응답이 없거나 sale_price=0이면 다른 채널로 폴백
            # (ABCmart 채널이 GrandStage 전용 상품에 빈 응답을 주는 케이스 등 대응)
            _needs_fallback = (
                not detail
                or detail.get("__product_not_found__")
                or int(detail.get("salePrice", 0) or 0) <= 0
            )
            if _needs_fallback:
                logger.info(
                    f"[ABCmart] 채널 폴백: {site_product_id} "
                    f"({primary_channel or '10001'} → {fallback_channel or '10001'})"
                )
                _alt_detail = await _fetch(fallback_channel)
                if _alt_detail and not _alt_detail.get("__product_not_found__"):
                    _alt_sale = int(_alt_detail.get("salePrice", 0) or 0)
                    if _alt_sale > 0:
                        detail = _alt_detail

            if not detail:
                return RefreshResult(
                    product_id=product_id,
                    error=f"ABCmart 상세 조회 실패: {site_product_id}",
                )
            if detail.get("__product_not_found__"):
                logger.warning(
                    f"[ABCmart] 소싱처 삭제 감지(판매종료) — 품절 처리: {site_product_id}"
                )
                return RefreshResult(
                    product_id=product_id,
                    new_sale_status="sold_out",
                    changed=True,
                    deleted_from_source=True,
                )

            # API는 품절/옵션 재고만 사용 — 가격은 DOM에서 수집 (API 가격 오류 방지)
            is_sold_out = detail.get("isOutOfStock", False)
            best_benefit_price = 0
            new_sale_price = 0
            new_original_price = 0

            # ── DOM 위임 — 가격·혜택가 모두 DOM에서 수집 ──
            try:
                from backend.domain.samba.proxy.sourcing_queue import (
                    SourcingQueue,
                    get_autotune_owner,
                )

                # GrandStage 상품도 site="ABCmart"로 잡 생성 — extension X-Allowed-Sites에
                # GrandStage가 없으면 allowed_set 필터로 잡 자체가 반환 안 됨.
                # 동일 도메인(a-rt.com)이므로 ABCmart 처리 경로로도 정확히 추출 가능.
                # URL만 GrandStage URL 사용해 올바른 채널가 수집.
                # 데몬 풀(X-Allowed-Sites=ABCmart) 우선, 없으면 확장앱 owner 폴백.
                from backend.domain.samba.proxy.daemon_pool import (
                    pick_daemon_owner,
                )

                _abc_owner = (
                    pick_daemon_owner("ABCmart")
                    or get_autotune_owner("ABCmart")
                    or None
                )
                if _prefer_grandstage:
                    _gs_url = f"https://grandstage.a-rt.com/product/new?prdtNo={site_product_id}&tChnnlNo=10002"
                    _dom_req, _dom_fut = SourcingQueue.add_detail_job(
                        "ABCmart",
                        site_product_id,
                        url=_gs_url,
                        owner_device_id=_abc_owner,
                    )
                else:
                    _dom_req, _dom_fut = SourcingQueue.add_detail_job(
                        "ABCmart", site_product_id, owner_device_id=_abc_owner
                    )
                # popup 윈도우 처리시간: ~25-30초 평균, 최대 ~45초
                # SSG 빈페이지 reload(최대 25s) + 다음 폴 사이클 대기 고려해 110s로 여유 확보
                _dom_ext = await asyncio.wait_for(_dom_fut, timeout=110)
                if isinstance(_dom_ext, dict) and _dom_ext.get("login_required"):
                    _reason = (
                        "창 미오픈"
                        if _dom_ext.get("gate_blocked")
                        else "로그인 미확인(DOM ambiguous)"
                    )
                    logger.warning(
                        f"[ABCmart] {_reason} → 갱신 차단: {site_product_id}"
                    )
                    return RefreshResult(
                        product_id=product_id,
                        error=f"ABCmart {_reason} — 갱신 차단",
                    )
                if isinstance(_dom_ext, dict) and _dom_ext.get("success"):
                    _bp = int(_dom_ext.get("best_benefit_price") or 0)
                    if _bp > 0:
                        logger.info(
                            f"[ABCmart] DOM 혜택가 수집: {site_product_id} → {_bp:,}원"
                        )
                        best_benefit_price = _bp
                    # 판매가·정상가도 DOM 값 사용 (API 가격은 사용하지 않음)
                    _dom_sale = int(_dom_ext.get("sale_price") or 0)
                    _dom_orig = int(_dom_ext.get("original_price") or 0)
                    if _dom_sale > 0:
                        new_sale_price = _dom_sale
                    if _dom_orig > 0:
                        new_original_price = _dom_orig
            except asyncio.TimeoutError:
                logger.warning(
                    f"[ABCmart] 확장앱 미응답(110s) → 갱신 차단: {site_product_id}"
                )
                return RefreshResult(
                    product_id=product_id,
                    error="ABCmart 확장앱 미응답 (110s 타임아웃) — 갱신 차단",
                )
            except Exception as _dom_err:
                logger.debug(f"[ABCmart] DOM 위임 예외: {site_product_id} — {_dom_err}")

            # 옵션 데이터 변환 (재고는 API에서 수집)
            new_options = None
            raw_options = detail.get("options", [])
            if raw_options:
                new_options = [
                    {
                        "name": opt.get("name", ""),
                        "price": opt.get("price", 0),
                        "stock": 0
                        if opt.get("isSoldOut")
                        else (opt.get("stock") or 99),
                        "isSoldOut": opt.get("isSoldOut", False),
                    }
                    for opt in raw_options
                ]

            from backend.domain.samba.collector.refresher import (
                count_stock_transitions,
            )

            old_options_ab = getattr(product, "options", None) or []
            _stock_changes = count_stock_transitions(old_options_ab, new_options or [])
            old_sale = getattr(product, "sale_price", 0) or 0
            old_status = getattr(product, "sale_status", "in_stock")
            new_sale_status = "sold_out" if is_sold_out else "in_stock"
            changed = (float(new_sale_price or 0) != float(old_sale or 0)) or (
                new_sale_status != old_status
            )

            return RefreshResult(
                product_id=product_id,
                new_sale_price=float(new_sale_price) if new_sale_price else None,
                new_original_price=float(new_original_price)
                if new_original_price
                else None,
                new_cost=float(best_benefit_price) if best_benefit_price else None,
                new_sale_status=new_sale_status,
                new_options=new_options,
                changed=changed,
                stock_changed=_stock_changes > 0,
            )

        except Exception as e:
            logger.error(f"[ABCmart] 갱신 실패: {site_product_id} — {e}")
            return RefreshResult(
                product_id=product_id,
                error=f"ABCmart 갱신 실패: {e}",
            )
