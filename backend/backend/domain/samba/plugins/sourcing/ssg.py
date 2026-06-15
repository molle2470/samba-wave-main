"""SSG(신세계몰) 소싱처 플러그인."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from backend.domain.samba.plugins.sourcing_base import SourcingPlugin

if TYPE_CHECKING:
    from backend.domain.samba.collector.refresher import RefreshResult

logger = logging.getLogger(__name__)


class SSGPlugin(SourcingPlugin):
    """SSG 소싱처 플러그인.

    SSG(신세계몰)는 robots.txt가 엄격하므로 보수적 간격으로 요청한다.
    bestBenefitPrice(최대혜택가)를 new_cost에 반영하여
    정책 적용 시 실질 매입가 기준으로 마진 계산이 가능하다.

    concurrency=1: 차단 강도가 높아 동시 1개만 요청
    request_interval=1.0: 요청 간 1초 딜레이 (보수적)
    """

    site_name = "SSG"
    concurrency = 1
    request_interval = 1.0

    async def search(self, keyword: str, **filters) -> list[dict]:
        """SSG 키워드 검색."""
        from backend.domain.samba.proxy.ssg_sourcing import SSGSourcingClient

        client = SSGSourcingClient()
        page = filters.get("page", 1)
        size = filters.get("size", 40)
        return await self.safe_call(
            client.search_products(keyword, page=page, size=size, **filters)
        )

    async def scan_categories(
        self,
        keyword: str,
        *,
        selected_brands: list[str] | None = None,
        brand_ids: list[str] | None = None,
        brand_total: int = 0,
        log_fn=None,
        proxy_urls: list[str] | None = None,
    ) -> dict:
        """SSG 카테고리 스캔 — categoryFilter 트리 플래튼 또는 상품 샘플링."""
        from backend.domain.samba.proxy.ssg_sourcing import SSGSourcingClient

        client = SSGSourcingClient()
        return await client.scan_categories(
            keyword,
            selected_brands=selected_brands,
            brand_ids=brand_ids,
            brand_total=brand_total,
            log_fn=log_fn,
            proxy_urls=proxy_urls,
        )

    async def discover_brands(self, keyword: str) -> dict:
        """SSG 브랜드 탐색 — brandFilter에서 브랜드 목록 추출."""
        from backend.domain.samba.proxy.ssg_sourcing import SSGSourcingClient

        client = SSGSourcingClient()
        return await client.discover_brands(keyword)

    async def get_detail(self, site_product_id: str) -> dict:
        """SSG 상품 상세 조회."""
        from backend.domain.samba.proxy.ssg_sourcing import SSGSourcingClient

        client = SSGSourcingClient()
        return await self.safe_call(client.get_product_detail(site_product_id))

    async def refresh(self, product) -> "RefreshResult":
        """가격/재고 갱신 — SSG 서버사이드 차단 우회를 위해 확장앱 SourcingQueue 위임."""
        import asyncio

        from backend.domain.samba.collector.refresher import RefreshResult
        from backend.domain.samba.proxy.sourcing_queue import SourcingQueue
        from backend.domain.samba.proxy.ssg_sourcing import SSGSourcingClient

        product_id = getattr(product, "id", "")
        site_product_id = getattr(product, "site_product_id", "") or getattr(
            product, "siteProductId", ""
        )

        if not site_product_id:
            return RefreshResult(
                product_id=product_id,
                error="SSG 상품 ID 없음",
            )

        try:
            client = SSGSourcingClient()
            detail: dict = {}

            # SSG는 서버사이드 직접 HTTP 차단 → 확장앱 위임 (worker.py 동일 패턴)
            # 타임아웃 60초: 병렬 처리(3개 탭) + reCAPTCHA/AJAX 지연을 감안해 충분한 여유 확보
            # owner_device_id 미전달 → SourcingQueue가 _autotune_owner_device_id(글로벌) 폴백 →
            # 오토튠 가동 중에는 실행 개시 PC 확장앱에서만 탭이 열리고 다른 PC 는 로그만 보게 됨.
            # 이전엔 owner_device_id="" 로 강제해 어떤 PC든 탭이 열리는 누수가 있었으나
            # "실행 개시 PC 만 창" 요구사항과 충돌하여 제거함(2026-04-29).
            # SourcingQueue는 requestId 단위 단일 resolver라 콜백 중복 자체는 없음.
            from backend.domain.samba.collector.refresher import _current_refresh_source

            _is_manual = _current_refresh_source.get("autotune") == "manual"
            # 데몬 풀(X-Allowed-Sites=SSG) 우선, 없으면 글로벌 owner 폴백.
            from backend.domain.samba.proxy.daemon_pool import pick_daemon_owner

            _ssg_owner = pick_daemon_owner("SSG")
            if _ssg_owner:
                _req_id, _future = SourcingQueue.add_detail_job(
                    "SSG",
                    site_product_id,
                    owner_device_id=_ssg_owner,
                    priority=_is_manual,
                )
            else:
                _req_id, _future = SourcingQueue.add_detail_job(
                    "SSG", site_product_id, priority=_is_manual
                )
            # 타임아웃 150s: 확장앱 슬롯 2개 × 아이템당 ~45s = 3배치 = 135s + 여유
            _ext_result = await asyncio.wait_for(_future, timeout=150)

            # 임직원/사업자 회원 전용 상품 — 확장앱이 staffOnly:true 명시 신호 전송
            # (success:false 케이스이지만 일반 고객 구매 불가이므로 sold_out으로 자동 정리)
            if isinstance(_ext_result, dict) and _ext_result.get("staffOnly"):
                logger.info(
                    f"[SSG] 임직원 전용 상품(확장앱 신호) → sold_out 처리: {site_product_id}"
                )
                return RefreshResult(
                    product_id=product_id,
                    new_sale_status="sold_out",
                    new_options=[],
                    changed=True,
                    stock_changed=True,
                )

            if isinstance(_ext_result, dict) and _ext_result.get("success"):
                # [데몬 분기] 헤드리스 데몬은 확장앱과 응답 형식이 다르다.
                #   확장앱: html + resultItemObj (원본) 전송 → 아래 파싱 로직이 처리
                #   데몬:   sale_price/best_benefit_price/domSalePrice (파싱 완료값) 전송, html·resultItemObj 없음
                # ssg.py 가 확장앱 형식만 읽어 데몬 결과를 통째로 버리고 "파싱 실패"로
                # 떨어지던 버그 수정 — 데몬 응답이면 파싱필드로 detail 직접 구성.
                _daemon_sale = int(_ext_result.get("sale_price", 0) or 0)
                _daemon_benefit = int(_ext_result.get("best_benefit_price", 0) or 0)
                if (
                    not _ext_result.get("html")
                    and not _ext_result.get("resultItemObj")
                    and (_daemon_sale > 0 or _daemon_benefit > 0)
                ):
                    _d_sale = (
                        int(_ext_result.get("domSalePrice", 0) or 0)
                        or _daemon_sale
                        or _daemon_benefit
                    )
                    _d_card = int(_ext_result.get("domCardPrice", 0) or 0)
                    _d_orig = int(_ext_result.get("original_price", 0) or 0) or _d_sale
                    _d_opts = []
                    for _o in _ext_result.get("options", []) or []:
                        _nm = (_o.get("name") or "").strip()
                        if not _nm:
                            continue
                        _so = bool(_o.get("isSoldOut"))
                        _stk = _o.get("stock")
                        # 품절=0, 실재고 있으면 그대로, 불명(None)→99 (기본 재고 규칙)
                        _d_opts.append(
                            {
                                "name": _nm,
                                "price": _d_sale,
                                "stock": 0
                                if _so
                                else (_stk if _stk is not None else 99),
                                "isSoldOut": _so,
                            }
                        )
                    # '대표단품' 더미 옵션 제거 — 수집 경로와 동일 규칙 (실옵션 있을 때만)
                    from backend.domain.samba.proxy.ssg_sourcing import (
                        filter_daepyo_options as _fdo_d,
                    )

                    _d_opts = _fdo_d(_d_opts)
                    _all_sold = bool(_d_opts) and all(_o["isSoldOut"] for _o in _d_opts)
                    # detail 만 구성하고 아래 공통 finalization(가격/원가/옵션/변동판정)으로 흘려보냄.
                    # 데몬 응답엔 html·resultItemObj 가 없어 아래 확장앱 파싱 블록은 자연히 no-op.
                    detail = {
                        "salePrice": _d_sale,
                        "originalPrice": _d_orig,
                        "bestBenefitPrice": _d_card or _daemon_benefit or _d_sale,
                        "options": _d_opts,
                        "isOutOfStock": _all_sold,
                        "isSoldOut": _all_sold,
                    }
                _html = _ext_result.get("html", "")
                # 백엔드 폴백 — html 본문 기준 임직원 마커 매칭(확장앱 신호 누락 시 보호)
                from backend.domain.samba.proxy.ssg_sourcing import _is_staff_only

                if _html and _is_staff_only(_html):
                    logger.info(
                        f"[SSG] 임직원 전용 상품(html 폴백) → sold_out 처리: {site_product_id}"
                    )
                    return RefreshResult(
                        product_id=product_id,
                        new_sale_status="sold_out",
                        new_options=[],
                        changed=True,
                        stock_changed=True,
                    )
                if _html:
                    detail = (
                        client._parse_result_item_obj(_html, site_product_id, True)
                        or {}
                    )
                # ⚠️ sellprc 절대 salePrice에 사용 금지 ⚠️
                # resultItemObj.sellprc = 정상가(할인 전 원가). 판매가가 아님.
                # 6ff7484c에서 "AJAX 실시간 값이라 정확"하다고 잘못 추가됐으나 오류.
                # salePrice는 domSalePrice(DOM 직접 추출) → bestAmt → _parse 결과 순으로 사용.
                _rob = _ext_result.get("resultItemObj", {})
                _rob_sell = int(
                    _rob.get("sellprc", 0) or 0
                )  # = 정상가(originalPrice 용도만)
                _rob_best = int(_rob.get("bestAmt", 0) or 0)  # = 최적 할인가
                _dom_card = int(_ext_result.get("domCardPrice", 0) or 0)
                _dom_sale = int(
                    _ext_result.get("domSalePrice", 0) or 0
                )  # DOM 실제 판매가
                if detail or _rob_sell > 0:
                    # 판매가: domSalePrice(DOM 직추출) → bestAmt → _parse 결과 유지
                    if _dom_sale > 0:
                        detail["salePrice"] = _dom_sale
                    elif _rob_best > 0 and not detail.get("salePrice"):
                        detail["salePrice"] = _rob_best
                    # 정상가: sellprc 사용 (할인 전 원가)
                    _rob_orig = int(
                        _rob.get("norprc", 0) or _rob.get("orgPrc", 0) or _rob_sell or 0
                    )
                    if _rob_orig > 0:
                        detail["originalPrice"] = _rob_orig
                    # 원가(bestBenefitPrice): domCardPrice → bestAmt(확장앱) → bestBenefitPrice(HTML) → domSalePrice → sellprc
                    _cur_benefit = int(detail.get("bestBenefitPrice", 0) or 0)
                    _orig_price = int(detail.get("originalPrice", 0) or _rob_sell or 0)
                    _ext_benefit = _rob_best or _dom_sale  # 확장앱 실시간값
                    if _dom_card > 0:
                        detail["bestBenefitPrice"] = _dom_card
                    elif _ext_benefit > 0 and (
                        _cur_benefit == 0
                        or (_orig_price > 0 and _cur_benefit >= _orig_price)
                    ):
                        # HTML 파싱이 정상가로 fallback됐거나 미설정이면 확장앱 실시간값 우선
                        detail["bestBenefitPrice"] = _ext_benefit
                    elif not _cur_benefit:
                        detail["bestBenefitPrice"] = _rob_sell

                # _parse_result_item_obj 실패 시 (dept.ssg.com AJAX 로드): resultItemObj 폴백
                if not detail:
                    _ext_obj = _ext_result.get("resultItemObj", {})
                    _item_nm = _ext_obj.get("itemNm", "")
                    if _item_nm and _html:
                        # sellprc = 정상가, bestAmt = 최적가, domSalePrice = DOM 실제 판매가
                        _sell = (
                            _dom_sale
                            or int(_ext_obj.get("bestAmt", 0) or 0)
                            or int(_ext_obj.get("sellprc", 0) or 0)
                        )
                        _opts = client._parse_layered_select_options(
                            _html, base_price=_sell
                        )
                        _sold = (
                            all(o.get("isSoldOut", False) for o in _opts)
                            if _opts
                            else False
                        )
                        _best_amt = int(_ext_obj.get("bestAmt", 0) or 0)
                        _dom_card_fb = int(_ext_result.get("domCardPrice", 0) or 0)
                        _benefit = (
                            _dom_card_fb if _dom_card_fb > 0 else (_best_amt or _sell)
                        )
                        _orig = int(
                            _ext_obj.get("norprc", 0)
                            or _ext_obj.get("orgPrc", 0)
                            or _ext_obj.get("sellprc", 0)
                            or 0
                        )
                        for _opt in _opts:
                            if not _opt.get("price"):
                                _opt["price"] = _sell
                        detail = {
                            "salePrice": _sell,
                            "originalPrice": _orig or _sell,
                            "bestBenefitPrice": _benefit,
                            "options": _opts,
                            "isOutOfStock": _sold,
                            "isSoldOut": _sold,
                        }
                # domOptions(JS 렌더링 후 DOM 파싱) 또는 uitemOptions로 실재고 보정
                _uitem_opts = _ext_result.get("uitemOptions", [])
                _dom_opts = _ext_result.get("domOptions", [])
                if detail.get("options"):
                    _has_layered_uitem_names = any(
                        "/" in str(o.get("name", "")) for o in _uitem_opts
                    )
                    _has_layered_detail_names = any(
                        "/" in str(o.get("name", "")) for o in detail["options"]
                    )
                    if (
                        _uitem_opts
                        and _has_layered_uitem_names
                        and (
                            not _has_layered_detail_names
                            or len(detail["options"]) < len(_uitem_opts)
                        )
                    ):
                        _price_fallback = int(detail.get("salePrice", 0) or 0)

                        def _build_uitem_opt(_opt: dict) -> dict:
                            _nm = _opt.get("name", "")
                            _entry = {
                                "name": _nm,
                                "price": int(_opt.get("price", 0) or 0)
                                or _price_fallback,
                                "stock": _opt.get("usablInvQty", 0),
                                "isSoldOut": _opt.get("isSoldOut", False),
                            }
                            # uitemOptions에 이미 depth 정보가 있으면 그대로 전달
                            if _opt.get("optionDepth"):
                                _entry["optionDepth"] = _opt["optionDepth"]
                                for _k in ("optionName1", "optionName2", "optionName3"):
                                    if _opt.get(_k):
                                        _entry[_k] = _opt[_k]
                            elif "/" in _nm:
                                _parts = _nm.split("/", 2)
                                _entry["optionDepth"] = len(_parts)
                                for _i, _p in enumerate(_parts, start=1):
                                    _entry[f"optionName{_i}"] = _p
                            return _entry

                        from backend.domain.samba.proxy.ssg_sourcing import (
                            filter_daepyo_options as _fdo_u,
                        )

                        detail["options"] = _fdo_u(
                            [
                                _build_uitem_opt(_opt)
                                for _opt in _uitem_opts
                                if _opt.get("name")
                            ]
                        )
                    if _dom_opts:
                        # DOM 파싱 결과 우선 — "남은수량 N" 실재고 반영
                        _dom_map = {o["name"]: o for o in _dom_opts if o.get("name")}
                        if not any(
                            _name in _dom_map
                            for _name in (
                                _opt.get("name", "") for _opt in detail["options"]
                            )
                        ):
                            _prefixes = {
                                _name.split("/", 1)[0]
                                for _name in (
                                    _opt.get("name", "") for _opt in detail["options"]
                                )
                                if "/" in _name
                            }
                            if len(_prefixes) == 1:
                                _prefix = next(iter(_prefixes))
                                _dom_map = {
                                    f"{_prefix}/{_name}": _opt
                                    for _name, _opt in _dom_map.items()
                                }
                        for _opt in detail["options"]:
                            _dom = _dom_map.get(_opt.get("name", ""))
                            if _dom:
                                if _dom.get("isSoldOut"):
                                    _opt["isSoldOut"] = True
                                    _opt["stock"] = 0
                                elif _dom.get("stock") is not None:
                                    _opt["isSoldOut"] = False
                                    _opt["stock"] = _dom["stock"]
                    elif _uitem_opts:
                        # DOM 파싱 없을 때 uitemOptions의 usablInvQty 폴백
                        _stock_map = {
                            o["name"]: o for o in _uitem_opts if o.get("name")
                        }
                        for _opt in detail["options"]:
                            _u = _stock_map.get(_opt.get("name", ""))
                            if _u:
                                _qty = _u.get("usablInvQty", 0)
                                _opt["isSoldOut"] = _qty == 0
                                _opt["stock"] = _qty if _qty > 0 else 0
                    _has_saleable_option = any(
                        (not _opt.get("isSoldOut", False))
                        and (_opt.get("stock") or 0) > 0
                        for _opt in detail["options"]
                    )
                    if _has_saleable_option:
                        detail["isOutOfStock"] = False
                        detail["isSoldOut"] = False
                    elif all(
                        _opt.get("isSoldOut", False) for _opt in detail["options"]
                    ):
                        detail["isOutOfStock"] = True
                        detail["isSoldOut"] = True

            if not detail:
                _ext_msg = ""
                if isinstance(_ext_result, dict) and not _ext_result.get("success"):
                    _ext_msg = (_ext_result.get("message") or "").strip()
                    if _ext_result.get("blocked"):
                        _ext_msg = "SSG 차단됨 (reCAPTCHA) — 잠시 후 재시도 해주세요"
                return RefreshResult(
                    product_id=product_id,
                    error=_ext_msg
                    or f"SSG 상세 조회 실패 (데몬/DOM위임 미응답 또는 파싱 실패): {site_product_id}",
                )

            new_sale_price = detail.get("salePrice", 0)
            new_original_price = detail.get("originalPrice", 0)
            is_sold_out = detail.get("isOutOfStock", False) or detail.get(
                "isSoldOut", False
            )

            # bestBenefitPrice → new_cost (실질 매입가)
            # domCardPrice(확장앱이 DOM에서 직접 추출한 카드혜택가) 최우선 강제 적용 —
            # 검증(2026-05-05): 확장앱이 정확한 카드혜택가 전송하지만 위쪽 분기들에서
            # detail/_rob_sell 조건 미충족 시 _dom_card 무시되어 cost가 bestAmt로 저장.
            # 어떤 코드 경로로도 _dom_card가 우선 적용되도록 최종 시점에 강제.
            _final_dom_card = (
                int(_ext_result.get("domCardPrice", 0) or 0)
                if isinstance(_ext_result, dict)
                else 0
            )
            best_benefit_price = (
                _final_dom_card
                if _final_dom_card > 0
                else detail.get("bestBenefitPrice", 0)
            )
            # SSG 카드혜택가는 결제금액 7만원 이상에서만 적용 — 7만원 미만 단품은 카드할인을
            # 못 받으므로 판매가(카드할인 전)를 원가로 한다(#430, 수집 게이트와 동일 정책).
            # refresh 에도 적용해 오토튠 갱신 시 cost 가 카드혜택가로 되돌아가지 않게 한다.
            _ssg_list_price = int(new_sale_price or 0)
            if 0 < _ssg_list_price < 70000:
                best_benefit_price = _ssg_list_price

            # 옵션 데이터 변환
            new_options = None
            raw_options = detail.get("options", [])
            if raw_options:
                new_options = [
                    {
                        **{k: v for k, v in opt.items() if not str(k).startswith("_")},
                        "name": opt.get("name", ""),
                        "price": opt.get("price", 0),
                        "stock": 0
                        if opt.get("isSoldOut")
                        else (opt.get("stock") if opt.get("stock") is not None else 99),
                        "isSoldOut": opt.get("isSoldOut", False),
                    }
                    for opt in raw_options
                ]

            # 옵션별 원가 계산 (카드할인율 × 옵션가격)
            if new_options and new_sale_price and best_benefit_price:
                _card_ratio = best_benefit_price / new_sale_price
                for _opt in new_options:
                    _opt_price = _opt.get("price", 0)
                    if _opt_price > 0:
                        _opt["cost"] = round(_opt_price * _card_ratio)
                # 제품 레벨 원가 = 가장 비싼 판매 가능 옵션 기준 (보수적 마진 보호)
                _max_price = max(
                    (
                        o.get("price", 0)
                        for o in new_options
                        if not o.get("isSoldOut", False)
                    ),
                    default=0,
                )
                if _max_price > 0:
                    best_benefit_price = round(_max_price * _card_ratio)

            # 변동/재고변동 정확 판정 — 옵션별 0 경계 전환을 stock_changed로 인정
            from backend.domain.samba.collector.refresher import (
                count_stock_transitions,
            )

            old_options_ssg = getattr(product, "options", None) or []
            _stock_changes = count_stock_transitions(old_options_ssg, new_options or [])
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

        except asyncio.TimeoutError:
            logger.warning(
                f"[SSG] 갱신 타임아웃 60초 (큐 적체/미연결): {site_product_id}"
            )
            return RefreshResult(
                product_id=product_id,
                error=f"SSG 갱신 타임아웃 60초 (확장앱 큐 적체 또는 미연결): {site_product_id}",
            )
        except Exception as e:
            logger.error(f"[SSG] 갱신 실패: {site_product_id} — {e}")
            return RefreshResult(
                product_id=product_id,
                error=f"SSG 갱신 실패: {e}",
            )
