"""KREAM 마켓 플러그인.

기존 dispatcher._handle_kream 로직을 플러그인 구조로 추출.
KREAM은 token/cookie를 settings에서 로드하므로 _load_auth 오버라이드.
사이즈별 매도 입찰(ask) 방식으로 등록.
"""

from __future__ import annotations

from typing import Any

from backend.domain.samba.plugins.market_base import MarketPlugin


async def _get_setting(session, key: str):
    """samba_settings 테이블에서 설정값 조회 후 즉시 커밋 — idle in transaction 방지."""
    from backend.domain.samba.forbidden.model import SambaSettings
    from sqlmodel import select

    stmt = select(SambaSettings).where(SambaSettings.key == key)
    result = await session.execute(stmt)
    row = result.scalars().first()
    val = row.value if row else None
    try:
        await session.commit()
    except Exception:
        pass
    return val


class KreamPlugin(MarketPlugin):
    market_type = "kream"
    policy_key = "KREAM"
    required_fields = ["name", "sale_price"]

    async def _load_auth(self, session, account) -> dict | None:
        """KREAM 인증 로드 — settings에서 token/cookie 우선 조회.

        KREAM은 일반 마켓과 달리 계정 필드가 아닌
        kream_token / kream_cookie / store_kream 설정에서 인증정보를 가져온다.
        """
        token = await _get_setting(session, "kream_token") or ""
        cookie = await _get_setting(session, "kream_cookie") or ""

        # token/cookie가 없으면 store_kream 설정에서 폴백.
        # (2026-05-25) resolver 위임 — find_default('kream') 우선.
        if not token and not cookie:
            from backend.domain.samba.account.resolver import resolve_market_creds

            creds = await resolve_market_creds(
                session, None, market_type="kream", store_key="store_kream"
            )
            if creds:
                token = creds.get("token", "")
                if not cookie:
                    cookie = creds.get("cookie", "")

        if not token:
            return None

        return {"token": str(token), "cookie": str(cookie)}

    def transform(self, product: dict, category_id: str, **kwargs) -> dict:
        """KREAM은 변환 없이 원본 데이터 사용 (매도 입찰 방식)."""
        return product

    async def execute(
        self,
        session,
        product: dict,
        creds: dict,
        category_id: str,
        account,
        existing_no: str,
    ) -> dict[str, Any]:
        """KREAM 매도 입찰 등록 — 사이즈별 ask bidding."""
        from backend.domain.samba.proxy.kream import KreamClient

        token = creds.get("token", "")
        cookie = creds.get("cookie", "")

        if not token:
            return {"success": False, "message": "KREAM 인증 정보가 없습니다."}

        client = KreamClient(token=token, cookie=cookie)
        kream_data = product.get("kream_data") or {}
        # KREAM 상품 ID 추출 — DB 스네이크/카멜/kream_data 내부 모두 폴백
        product_id = (
            product.get("site_product_id")
            or product.get("siteProductId")
            or kream_data.get("product_id")
            or kream_data.get("siteProductId")
            or ""
        )
        product_id = str(product_id).strip()
        if not product_id:
            return {"success": False, "message": "KREAM 상품 ID가 없습니다."}

        # 사이즈별 매도 입찰 — 옵션 스키마는 {name, price, stock} 또는 {size, price}
        options = product.get("options") or []
        sale_type = "auction"
        results: list[dict[str, Any]] = []
        fallback_price = 0
        try:
            fallback_price = int(product.get("sale_price", 0) or 0)
        except (TypeError, ValueError):
            fallback_price = 0

        for opt in options:
            size = (opt.get("name") or opt.get("size") or "").strip()
            try:
                price = int(opt.get("price") or fallback_price)
            except (TypeError, ValueError):
                price = fallback_price
            if size and price > 0:
                r = await client.create_ask(product_id, size, price, sale_type)
                results.append(r)

        if not results and fallback_price > 0:
            # 단일 상품 (옵션 없음) — KREAM이 거부할 수 있으나 응답 메시지로 노출
            r = await client.create_ask(
                product_id, "ONE_SIZE", fallback_price, sale_type
            )
            results.append(r)

        ok_count = sum(1 for r in results if r.get("success"))
        first_ask_id = ""
        for r in results:
            if r.get("success"):
                data = r.get("data") or {}
                if isinstance(data, dict):
                    first_ask_id = str(data.get("ask_id") or data.get("id") or "")
                if first_ask_id:
                    break

        success = ok_count > 0
        if not success:
            err_msg = ""
            for r in results:
                if not r.get("success") and r.get("message"):
                    err_msg = r.get("message", "")
                    break
            return {
                "success": False,
                "message": err_msg or "KREAM 입찰 등록 실패",
                "data": results,
            }

        # _extract_market_product_no가 인식하도록 product_no 키 노출
        return {
            "success": True,
            "message": f"KREAM {ok_count}건 입찰 등록",
            "product_no": product_id,
            "ask_id": first_ask_id,
            "data": results,
        }
