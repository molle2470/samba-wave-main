"""마켓 카테고리 API 동기화 Mixin."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any, Dict, List, Optional, TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from backend.domain.samba.category.rules import _OVERSEAS_KEYWORDS

logger = logging.getLogger(__name__)


class CategorySyncMixin:
    """7개 마켓 카테고리 API 동기화."""

    async def _get_account(
        self, market_type: str, session: "AsyncSession"
    ) -> "SambaMarketAccount":  # noqa: F821
        """계정관리 테이블에서 마켓 계정 조회 (활성 계정 우선)."""
        from sqlmodel import select
        from backend.domain.samba.account.model import SambaMarketAccount

        stmt = (
            select(SambaMarketAccount)
            .where(SambaMarketAccount.market_type == market_type)
            .where(SambaMarketAccount.is_active.is_(True))
            .limit(1)
        )
        result = await session.execute(stmt)
        account = result.scalars().first()
        if not account:
            raise ValueError(
                f"{market_type} 계정이 등록되지 않았습니다. 계정관리에서 먼저 추가해주세요."
            )
        return account

    async def sync_market_from_api(
        self, market_type: str, session: "AsyncSession"
    ) -> Dict[str, Any]:
        """마켓 API에서 카테고리를 실시간 조회하여 DB에 저장.

        계정관리 테이블(SambaMarketAccount)에서 인증 정보를 직접 읽는다.
        """
        # ssg_std는 ssg 계정을 공유 — "ssg" market_type으로 계정 조회
        account_market_type = "ssg" if market_type == "ssg_std" else market_type
        account = await self._get_account(account_market_type, session)
        categories: List[str] = []
        code_map: Optional[Dict[str, str]] = None

        # 모든 마켓이 (categories, code_map) 튜플 반환
        sync_methods = {
            "smartstore": self._sync_smartstore,
            "lotteon": self._sync_lotteon,
            "ssg": self._sync_ssg_display,  # 전시카테고리
            "ssg_std": self._sync_ssg,  # 표준카테고리 (계정은 "ssg"로 조회)
            "gsshop": self._sync_gsshop,
            "coupang": self._sync_coupang,
            "11st": self._sync_elevenst,
            "lottehome": self._sync_lottehome,
            "cafe24": self._sync_cafe24,
            "gmarket": self._sync_esm_market,
            "auction": self._sync_esm_market,
        }
        method = sync_methods.get(market_type)
        if not method:
            raise ValueError(f"API 동기화를 지원하지 않는 마켓: {market_type}")

        result = await method(account)
        if isinstance(result, tuple):
            categories, code_map = result
        else:
            categories = result

        if not categories:
            raise ValueError(
                f"{market_type} 카테고리 조회 결과가 비어있습니다. 계정 인증 정보를 확인해주세요."
            )

        # DB에 저장 (기존 데이터 교체)
        existing = await self.tree_repo.get_by_site(market_type)
        if existing:
            existing.cat1 = categories
            if code_map is not None:
                existing.cat2 = code_map
            existing.updated_at = datetime.now(UTC)
            self.tree_repo.session.add(existing)
        else:
            tree = await self.tree_repo.create_async(
                site_name=market_type, cat1=categories
            )
            if code_map is not None:
                tree.cat2 = code_map
                self.tree_repo.session.add(tree)
        await self.tree_repo.session.commit()

        logger.info(
            "[카테고리 동기화] %s: %d개 카테고리 저장", market_type, len(categories)
        )
        return {"count": len(categories), "updated_at": datetime.now(UTC).isoformat()}

    async def sync_all_markets(self, session: "AsyncSession") -> Dict[str, Any]:
        """등록된 모든 마켓의 카테고리를 API에서 일괄 동기화.

        각 마켓별 60초 타임아웃. 계정 없는 마켓은 빠르게 스킵.
        """
        markets = [
            "smartstore",
            "coupang",
            "11st",
            "lotteon",
            "lottehome",
            "ssg",
            "ssg_std",
            "gsshop",
            "cafe24",
        ]
        results: Dict[str, Any] = {}
        for market in markets:
            try:
                result = await asyncio.wait_for(
                    self.sync_market_from_api(market, session),
                    timeout=120,
                )
                results[market] = {"ok": True, **result}
                logger.info(
                    "[카테고리 동기화] %s 완료: %d개", market, result.get("count", 0)
                )
            except asyncio.TimeoutError:
                results[market] = {"ok": False, "error": "타임아웃 (120초 초과)"}
                logger.warning("[카테고리 동기화] %s 타임아웃", market)
            except Exception as e:
                results[market] = {"ok": False, "error": str(e)}
                logger.warning("[카테고리 동기화] %s 실패: %s", market, e)
        return results

    async def _sync_smartstore(self, account) -> tuple:
        """스마트스토어 카테고리 동기화 (Naver Commerce API). (카테고리목록, 코드맵) 반환."""
        from backend.domain.samba.proxy.smartstore import SmartStoreClient

        extra = account.additional_fields or {}
        client_id = extra.get("clientId") or account.api_key or ""
        client_secret = extra.get("clientSecret") or account.api_secret or ""
        if not client_id or not client_secret:
            raise ValueError("스마트스토어 clientId/clientSecret이 없습니다")

        client = SmartStoreClient(client_id, client_secret)
        # 전체 카테고리 조회 (비-리프 포함 — 코드맵 완전성 보장)
        raw = await client.get_categories(last_only=False)

        categories: List[str] = []
        code_map: Dict[str, str] = {}
        if isinstance(raw, list):
            for item in raw:
                whole = item.get("wholeCategoryName", "")
                cat_id = str(item.get("id", ""))
                if whole:
                    normalized = " > ".join(
                        seg.strip() for seg in whole.split(">") if seg.strip()
                    )
                    categories.append(normalized)
                    if cat_id:
                        code_map[normalized] = cat_id
        return categories, code_map if code_map else None

    async def _sync_coupang(self, account) -> tuple:
        """쿠팡 카테고리 동기화 (Wing API). (카테고리목록, 코드맵) 튜플 반환.

        쿠팡 API는 트리 구조로 반환하므로 평탄화하여 경로 문자열 + 코드맵 생성.
        """
        from backend.domain.samba.proxy.coupang import CoupangClient

        extra = account.additional_fields or {}
        client = CoupangClient(
            access_key=extra.get("accessKey") or account.api_key or "",
            secret_key=extra.get("secretKey") or account.api_secret or "",
            vendor_id=extra.get("vendorId") or account.seller_id or "",
        )
        raw = await client.get_categories()
        root = raw.get("data", raw) if isinstance(raw, dict) else {}
        if not isinstance(root, dict):
            return [], None

        # 트리 평탄화: 리프 노드의 경로 문자열 + 코드 추출
        categories: List[str] = []
        code_map: Dict[str, str] = {}

        def flatten(node: dict, path: str = "") -> None:
            code = node.get("displayItemCategoryCode", 0)
            name = node.get("name", "")
            if name == "ROOT":
                name = ""
            current = f"{path} > {name}" if path and name else (path or name)
            children = node.get("child", [])
            if not children and current and code:
                categories.append(current)
                code_map[current] = str(code)
            for c in children:
                flatten(c, current)

        flatten(root)
        return categories, code_map

    async def _sync_elevenst(self, account) -> tuple:
        """11번가 카테고리 동기화. (카테고리목록, {경로: 숫자코드}) 튜플 반환.

        11번가 API 응답은 ns2:category XML로 계층 구조를 반환한다.
        depth / dispNm / dispNo / parentDispNo 필드를 파싱하여
        전체 경로 문자열과 숫자 코드 매핑을 생성한다.
        """
        from xml.etree import ElementTree as ET

        url = "https://api.11st.co.kr/rest/cateservice/category"
        headers = {
            "openapikey": account.api_key or "",
            "Accept": "application/xml",
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, headers=headers)
            logger.info("[11번가] GET /cateservice/category → %s", resp.status_code)

        if not resp.is_success:
            raise ValueError(f"11번가 카테고리 API 에러: HTTP {resp.status_code}")

        # XML 파싱 (네임스페이스 제거)
        xml_text = resp.text
        # 네임스페이스 프리픽스 제거하여 파싱 단순화
        xml_text = xml_text.replace("ns2:", "")
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            logger.error("[11번가] 카테고리 XML 파싱 실패")
            return [], {}

        # dispNo → dispNm 매핑 + parentDispNo 관계 구축
        node_map: Dict[str, str] = {}  # dispNo → dispNm
        parent_map: Dict[str, str] = {}  # dispNo → parentDispNo
        leaf_nodes: list = []  # leaf 카테고리 dispNo 목록

        for cat in root.findall("category"):
            disp_no = (cat.findtext("dispNo") or "").strip()
            disp_nm = (cat.findtext("dispNm") or "").strip()
            parent_no = (cat.findtext("parentDispNo") or "0").strip()
            is_leaf = (cat.findtext("leafYn") or "N").strip()

            if disp_no and disp_nm:
                node_map[disp_no] = disp_nm
                parent_map[disp_no] = parent_no
                if is_leaf == "Y":
                    leaf_nodes.append(disp_no)

        # 경로 생성 함수
        def build_path(disp_no: str) -> str:
            parts = []
            current = disp_no
            while current and current != "0" and current in node_map:
                parts.append(node_map[current])
                current = parent_map.get(current, "0")
            parts.reverse()
            return " > ".join(parts)

        # leaf 카테고리만 경로 생성 (등록 가능한 최하위 카테고리)
        categories = []
        code_map: Dict[str, str] = {}
        for disp_no in leaf_nodes:
            path = build_path(disp_no)
            if path:
                # 해외쇼핑 트리 카테고리 제외 — 국내 전용 운영
                if any(kw in path for kw in _OVERSEAS_KEYWORDS):
                    continue
                categories.append(path)
                code_map[path] = disp_no

        logger.info(
            "[11번가] 카테고리 파싱 완료: %d개 (leaf), 코드맵 %d개",
            len(categories),
            len(code_map),
        )
        return categories, code_map

    async def _sync_lotteon(self, account) -> tuple:
        """롯데ON 카테고리 동기화. (카테고리목록, 코드맵) 반환.

        depth 파라미터만으로는 API가 100개씩 페이지네이션하여 e쿠폰만 반환.
        parent_id BFS로 각 루트별 자식을 순회해 전체 트리를 수집.
        """
        from backend.domain.samba.proxy.lotteon import LotteonClient

        extra = account.additional_fields or {}
        api_key = extra.get("apiKey") or account.api_key or ""
        if not api_key:
            raise ValueError("롯데ON API Key가 없습니다")
        client = LotteonClient(api_key=api_key)

        node_map: Dict[str, str] = {}  # std_cat_id → std_cat_nm
        parent_map: Dict[str, str] = {}  # std_cat_id → upr_std_cat_id
        leaf_ids: List[str] = []

        def _add_item(d: dict, pid: str) -> str:
            cat_id = d.get("std_cat_id", "")
            cat_nm = d.get("std_cat_nm", "")
            if cat_id and cat_nm and cat_id not in node_map:
                node_map[cat_id] = cat_nm
                parent_map[cat_id] = pid
            return cat_id

        # 1단계: depth=1 루트 카테고리 획득
        try:
            raw = await client.get_categories(depth="1")
            root_items = raw.get("itemList", [])
            logger.info("[롯데ON 동기화] depth=1 루트 %d개", len(root_items))
            if root_items:
                logger.info("[롯데ON 동기화] 루트 샘플: %s", str(root_items[0])[:250])
            for item in root_items:
                d = item.get("data", item)
                _add_item(d, "0")
        except Exception as e:
            logger.warning("[롯데ON 동기화] depth=1 실패: %s", e)

        if not node_map:
            raise ValueError(
                "롯데ON 카테고리 루트를 가져올 수 없습니다. 계정 인증 정보를 확인해주세요."
            )

        root_ids = list(node_map.keys())
        logger.info(
            "[롯데ON 동기화] 루트 카테고리: %s", [node_map[i] for i in root_ids[:10]]
        )

        # shared httpx 세션으로 TCP 연결 재사용
        _timeout = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)
        _sem = asyncio.Semaphore(15)

        async with httpx.AsyncClient(timeout=_timeout) as _http:

            async def _fetch(pid: str) -> tuple:
                async with _sem:
                    try:
                        result = await asyncio.wait_for(
                            client.get_categories(parent_id=pid, _shared_client=_http),
                            timeout=12.0,
                        )
                        return pid, result.get("itemList", [])
                    except Exception as e:
                        logger.warning(
                            "[롯데ON 동기화] parent=%s 실패: %s", pid, type(e).__name__
                        )
                        return pid, []

            # 2단계: 각 루트 자식을 병렬 조회
            d2_results = await asyncio.gather(*[_fetch(rid) for rid in root_ids])
            depth2_non_leaf: List[str] = []
            for parent_id, items in d2_results:
                for item in items:
                    d = item.get("data", item)
                    cid = _add_item(d, parent_id)
                    if cid:
                        if d.get("leaf_yn") == "Y":
                            leaf_ids.append(cid)
                        else:
                            depth2_non_leaf.append(cid)

            logger.info(
                "[롯데ON 동기화] depth=2 완료: 비리프=%d개, leaf=%d개",
                len(depth2_non_leaf),
                len(leaf_ids),
            )

            # 3단계: SKIP 키워드 제외 모든 비리프 카테고리 조회
            # 화이트리스트(_NEED_KEYWORDS) 방식은 누락 카테고리 발생(예: 구기/라켓>축구 하위 미수집).
            # SKIP 키워드만 차단하고 나머지는 끝까지 탐색.
            _SKIP_KEYWORDS = (
                "e쿠폰",
                "티켓",
                "상품권",
                "쿠폰",
                "여행",
                "숙박",
                "항공",
                "렌터카",
            )
            depth2_to_fetch: List[str] = []
            for d2id in depth2_non_leaf:
                nm = node_map.get(d2id, "")
                parent_nm = node_map.get(parent_map.get(d2id, ""), "")
                path_nm = f"{parent_nm} {nm}"
                if any(kw in path_nm for kw in _SKIP_KEYWORDS):
                    leaf_ids.append(d2id)
                else:
                    depth2_to_fetch.append(d2id)

            logger.info("[롯데ON 동기화] depth=3 대상: %d개", len(depth2_to_fetch))

            if depth2_to_fetch:
                d3_results = await asyncio.gather(
                    *[_fetch(d2id) for d2id in depth2_to_fetch]
                )
                depth3_non_leaf: List[str] = []
                for parent_id, items in d3_results:
                    if not items:
                        leaf_ids.append(parent_id)
                        continue
                    for item in items:
                        d = item.get("data", item)
                        cid = _add_item(d, parent_id)
                        if cid:
                            if d.get("leaf_yn") == "Y":
                                leaf_ids.append(cid)
                            else:
                                depth3_non_leaf.append(cid)

                # depth=4 탐색 (가운>여성용 같이 4단계까지 있는 케이스)
                if depth3_non_leaf:
                    logger.info(
                        "[롯데ON 동기화] depth=4 대상: %d개", len(depth3_non_leaf)
                    )
                    d4_results = await asyncio.gather(
                        *[_fetch(d3id) for d3id in depth3_non_leaf]
                    )
                    for parent_id, items in d4_results:
                        if not items:
                            leaf_ids.append(parent_id)
                            continue
                        for item in items:
                            d = item.get("data", item)
                            cid = _add_item(d, parent_id)
                            if cid:
                                leaf_ids.append(cid)

        logger.info(
            "[롯데ON 동기화] 완료: node_map=%d개, leaf_ids=%d개",
            len(node_map),
            len(leaf_ids),
        )

        # leaf가 없으면 모든 노드를 leaf로
        if not leaf_ids:
            leaf_ids = list(node_map.keys())

        # 경로 생성
        def build_path(cat_id: str) -> str:
            parts = []
            current = cat_id
            while current and current != "0" and current in node_map:
                parts.append(node_map[current])
                current = parent_map.get(current, "0")
            parts.reverse()
            return " > ".join(parts)

        categories: List[str] = []
        code_map: Dict[str, str] = {}
        for cat_id in leaf_ids:
            path = build_path(cat_id)
            if path and path not in code_map:
                categories.append(path)
                code_map[path] = cat_id

        return categories, code_map if code_map else None

    async def _sync_cafe24(self, account) -> tuple:
        """카페24 카테고리 동기화. (카테고리목록, 코드맵) 반환.

        카페24 카테고리는 계층 구조 (parent_category_no)를 경로로 변환.
        """
        from backend.domain.samba.proxy.cafe24 import Cafe24Client

        extra = account.additional_fields or {}
        mall_id = extra.get("mallId") or account.seller_id or ""
        client_id = extra.get("clientId") or ""
        client_secret = extra.get("clientSecret") or account.api_secret or ""
        access_token = extra.get("accessToken") or account.api_key or ""
        refresh_token = extra.get("refreshToken") or ""

        if not mall_id:
            raise ValueError("카페24 Mall ID가 없습니다")
        if not client_id or not client_secret:
            raise ValueError("카페24 Client ID/Secret이 없습니다")

        client = Cafe24Client(
            mall_id=mall_id,
            client_id=client_id,
            client_secret=client_secret,
            access_token=access_token,
            refresh_token=refresh_token,
        )
        raw_cats = await client.get_categories()

        # 카테고리 번호 → 정보 매핑
        cat_by_no: Dict[int, dict] = {}
        for c in raw_cats:
            cat_no = c.get("category_no")
            if cat_no:
                cat_by_no[cat_no] = c

        # 경로 생성 함수
        def _build_path(cat_no: int) -> str:
            parts = []
            visited = set()
            current = cat_no
            while current and current in cat_by_no and current not in visited:
                visited.add(current)
                info = cat_by_no[current]
                parts.insert(0, info.get("category_name", ""))
                current = info.get("parent_category_no", 0)
            return " > ".join(p for p in parts if p)

        categories: List[str] = []
        code_map: Dict[str, str] = {}
        for c in raw_cats:
            cat_no = c.get("category_no")
            name = c.get("category_name", "")
            if not cat_no or not name:
                continue
            path = _build_path(cat_no)
            if path:
                categories.append(path)
                code_map[path] = str(cat_no)

        # 토큰 갱신 시 계정에 저장
        if client.access_token != access_token:
            extra["accessToken"] = client.access_token
            if client.refresh_token:
                extra["refreshToken"] = client.refresh_token
            account.additional_fields = extra
            self.tree_repo.session.add(account)

        return categories, code_map if code_map else None

    async def _sync_esm_market(self, account) -> tuple:
        """ESM Plus(지마켓/옥션) 카테고리 동기화. (카테고리목록, 코드맵) 반환.

        사전 수집된 JSON 파일이 있으면 즉시 로드, 없으면 API로 수집.
        """
        import json as _json
        from pathlib import Path as _Path

        market_type = account.market_type  # "gmarket" or "auction"
        file_name = (
            "esm_gmarket_cats.json"
            if market_type == "gmarket"
            else "esm_auction_cats.json"
        )
        json_path = _Path(__file__).resolve().parent / file_name

        if json_path.exists():
            # 사전 수집된 JSON 로드
            with open(json_path, encoding="utf-8") as f:
                tree = _json.load(f)  # {경로: 코드}
            categories = list(tree.keys())
            code_map = tree
            logger.info(f"[{market_type}] 카테고리 JSON 로드: {len(categories)}개")
            return categories, code_map

        # JSON 없으면 API로 수집
        from backend.domain.samba.proxy.esmplus import ESMPlusClient

        extra = account.additional_fields or {}
        seller_id = extra.get("apiKey") or extra.get("sellerId") or ""
        if not seller_id:
            raise ValueError(f"{market_type} 판매자 ID가 없습니다")

        from backend.domain.samba.proxy.esmplus import resolve_esm_credentials

        hosting_id, secret_key = await resolve_esm_credentials(self.session, account)
        if not hosting_id or not secret_key:
            raise ValueError(
                f"{market_type} ESM 인증정보 없음 — account.additional_fields / samba_settings.esm_credentials / ESMPLUS_HOSTING_ID env 중 하나 필요"
            )

        client = ESMPlusClient(hosting_id, secret_key, seller_id, site=market_type)
        tree = await client.fetch_category_tree(delay=0.5)

        # JSON 파일로 저장 (다음 동기화 시 빠른 로드용)
        with open(json_path, "w", encoding="utf-8") as f:
            _json.dump(tree, f, ensure_ascii=False, indent=2)
        logger.info(f"[{market_type}] 카테고리 API 수집 + JSON 저장: {len(tree)}개")

        categories = list(tree.keys())
        code_map = tree
        return categories, code_map

    async def _sync_lottehome(self, account) -> tuple:
        """롯데홈쇼핑 카테고리 동기화. (카테고리목록, 코드맵) 반환."""
        from backend.domain.samba.proxy.lottehome import LotteHomeClient

        extra = account.additional_fields or {}
        user_id = extra.get("userId") or account.seller_id or ""
        client = LotteHomeClient(
            user_id=user_id,
            password=extra.get("password") or account.api_secret or "",
            agnc_no=extra.get("agncNo") or user_id,  # 로그인ID = 업체번호 동일
            env=extra.get("env") or "prod",
        )
        raw = await client.search_categories()
        categories: List[str] = []
        code_map: Dict[str, str] = {}
        data = raw.get("data", {}) if isinstance(raw, dict) else {}
        items = data if isinstance(data, list) else []
        if isinstance(data, dict):
            for key in ("DispCatList", "dispCatList", "category", "list", "items"):
                val = data.get(key)
                if isinstance(val, list):
                    items = val
                    break
        for item in items:
            if isinstance(item, dict):
                name = (
                    item.get("dispCatNm")
                    or item.get("categoryName")
                    or item.get("name", "")
                )
                cat_id = str(
                    item.get("dispCatNo")
                    or item.get("categoryNo")
                    or item.get("id", "")
                )
                if name:
                    categories.append(name)
                    if cat_id:
                        code_map[name] = cat_id
            elif isinstance(item, str) and item:
                categories.append(item)
        return categories, code_map if code_map else None

    async def _sync_ssg_display(self, account) -> tuple:
        """SSG 전시카테고리 동기화. (카테고리목록, 코드맵) 반환.

        GET /common/0.1/displayCategory.ssg → result.displayCategorys[].category
        dispCtgPathNm(경로), dispCtgId(코드) 파싱.
        """
        from backend.domain.samba.proxy.ssg import SSGClient, SSGApiError

        extra = account.additional_fields or {}
        api_key = extra.get("apiKey") or account.api_key or ""
        if not api_key:
            raise ValueError("SSG API Key가 없습니다")
        store_id = extra.get("storeId") or "6004"
        client = SSGClient(api_key=api_key, site_no=store_id)

        categories: List[str] = []
        code_map: Dict[str, str] = {}
        page = 1
        page_size = 500

        while True:
            try:
                raw = await client.get_display_categories_all(
                    site_no=store_id, page=page, page_size=page_size
                )
            except SSGApiError as exc:
                if page == 1:
                    raise ValueError(f"SSG 전시카테고리 조회 실패: {exc}") from exc
                logger.warning(
                    "[SSG 전시카테고리] page %d 조회 실패, 기존 %d건 저장: %s",
                    page,
                    len(categories),
                    exc,
                )
                break

            result_obj = raw.get("result", {})
            if not isinstance(result_obj, dict):
                result_obj = {}
            display_categorys = result_obj.get("displayCategorys", [])
            items: list = []
            if isinstance(display_categorys, list):
                for wrapper in display_categorys:
                    if isinstance(wrapper, dict):
                        cat = wrapper.get("category", [])
                        if isinstance(cat, list):
                            items.extend(cat)
                        elif isinstance(cat, dict):
                            items.append(cat)

            if not items:
                if page == 1:
                    raise ValueError(
                        "SSG 전시카테고리 응답이 비어있습니다. API Key 및 사이트번호를 확인해주세요."
                    )
                break

            for item in items:
                if not isinstance(item, dict):
                    continue
                path = item.get("dispCtgPathNm", "")
                cat_id = str(item.get("dispCtgId") or "")
                if not path:
                    path = item.get("dispCtgNm", "")
                if not path:
                    continue
                normalized = " > ".join(
                    seg.strip() for seg in path.split(">") if seg.strip()
                )
                categories.append(normalized)
                if cat_id:
                    code_map[normalized] = cat_id

            logger.info(
                f"[SSG 전시카테고리] page {page}: {len(items)}건, 누적 {len(categories)}개"
            )
            if len(items) < page_size:
                break
            page += 1

        return categories, code_map if code_map else None

    async def _sync_ssg(self, account) -> tuple:
        """SSG 표준카테고리 동기화 v2 (페이징 + 경로 포함). (카테고리목록, 코드맵) 반환."""
        from backend.domain.samba.proxy.ssg import SSGClient, SSGApiError

        extra = account.additional_fields or {}
        api_key = extra.get("apiKey") or account.api_key or ""
        if not api_key:
            raise ValueError("SSG API Key가 없습니다")
        client = SSGClient(api_key=api_key)

        categories: List[str] = []
        code_map: Dict[str, str] = {}
        page = 1
        page_size = 500

        while True:
            try:
                raw = await client.get_categories_v2(page=page, page_size=page_size)
            except SSGApiError as exc:
                if page == 1:
                    raise ValueError(f"SSG 카테고리 조회 실패: {exc}") from exc
                logger.warning(
                    "[SSG] page %d 조회 실패, 기존 %d건 저장: %s",
                    page,
                    len(categories),
                    exc,
                )
                break

            # 실제 응답: {"result": {"stdctgs": [{"stdctg": [...items...]}]}}
            result_obj = raw.get("result", {})
            if not isinstance(result_obj, dict):
                result_obj = {}
            stdctgs_wrapper = result_obj.get("stdctgs", [])
            items: list = []
            if isinstance(stdctgs_wrapper, list) and stdctgs_wrapper:
                first = stdctgs_wrapper[0]
                if isinstance(first, dict):
                    stdctg = first.get("stdctg", [])
                    items = (
                        stdctg
                        if isinstance(stdctg, list)
                        else [stdctg]
                        if isinstance(stdctg, dict)
                        else []
                    )

            if not items:
                if page == 1:
                    raise ValueError(
                        "SSG 카테고리 응답이 비어있습니다. API Key를 확인해주세요."
                    )
                break

            for item in items:
                if not isinstance(item, dict):
                    continue
                path = item.get("stdCtgKeyPath", "")
                if not path:
                    parts = [
                        item.get("stdCtgLclsNm", ""),
                        item.get("stdCtgMclsNm", ""),
                        item.get("stdCtgSclsNm", ""),
                        item.get("stdCtgDclsNm", ""),
                    ]
                    path = " > ".join(p for p in parts if p)
                if not path:
                    continue
                normalized = " > ".join(
                    seg.strip() for seg in path.split(">") if seg.strip()
                )
                cat_id = str(item.get("stdCtgDclsId") or item.get("stdCtgSclsId") or "")
                categories.append(normalized)
                if cat_id:
                    code_map[normalized] = cat_id

            logger.info(
                f"[SSG 카테고리] page {page}: {len(items)}건, 누적 {len(categories)}개"
            )
            if len(items) < page_size:
                break
            page += 1

        return categories, code_map if code_map else None

    async def _sync_gsshop(self, account) -> tuple:
        """GS샵 카테고리 동기화. (카테고리목록, 코드맵) 반환."""
        from backend.domain.samba.proxy.gsshop import GsShopClient

        extra = account.additional_fields or {}
        env = extra.get("env") or "prod"
        aes_key = (
            extra.get("aesKey")
            or (extra.get("apiKeyProd") if env == "prod" else extra.get("apiKeyDev"))
            or account.api_key
            or ""
        )
        if not aes_key:
            raise ValueError("GS샵 AES Key가 없습니다")
        client = GsShopClient(
            sup_cd=extra.get("supCd") or account.seller_id or "",
            aes_key=aes_key,
            sub_sup_cd=extra.get("subSupCd") or "",
            env=env,
        )
        raw = await client.get_product_categories()
        categories: List[str] = []
        code_map: Dict[str, str] = {}
        # GS샵 응답: data.resultList[{lrgClsNm, midClsNm, smlClsNm, dtlClsNm, dtlClsCd}]
        data = raw.get("data", raw) if isinstance(raw, dict) else raw
        items = (
            data.get("resultList", [])
            if isinstance(data, dict)
            else (data if isinstance(data, list) else [])
        )
        for item in items:
            if not isinstance(item, dict):
                continue
            parts = [
                item.get("lrgClsNm", ""),
                item.get("midClsNm", ""),
                item.get("smlClsNm", ""),
                item.get("dtlClsNm", ""),
            ]
            path = " > ".join(p for p in parts if p)
            cat_id = item.get("dtlClsCd") or item.get("smlClsCd") or ""
            if path:
                categories.append(path)
                if cat_id:
                    code_map[path] = str(cat_id)
        return categories, code_map if code_map else None
