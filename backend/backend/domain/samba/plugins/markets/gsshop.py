"""GS샵 마켓 플러그인.

기존 dispatcher._handle_gsshop + _transform_for_gsshop 로직을 플러그인 구조로 추출.
인증 로드는 base._load_auth 가 처리하므로 execute 에서는 creds dict 사용.
"""

from __future__ import annotations

from typing import Any

from backend.domain.samba.plugins.market_base import MarketPlugin
from backend.utils import add_lazy_loading


async def _get_setting(session, key: str) -> Any:
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


def _transform_for_gsshop(
    product: dict[str, Any],
    category_id: str,
    gs_margin_rate: int = 0,
) -> dict[str, Any]:
    """수집 상품 → GS샵 형식 변환."""
    images = product.get("images") or []
    data: dict[str, Any] = {
        "prdNm": product.get("name", ""),
        "brndNm": product.get("brand", ""),
        "selPrc": int(product.get("sale_price", 0)),
        "dispCtgrNo": category_id,
        "prdCntntListCntntUrlNm": images[0] if images else "",
        "mobilBannerImgUrl": images[0] if images else "",
        "prdDetailCntnt": add_lazy_loading(
            product.get("detail_html", "") or f"<p>{product.get('name', '')}</p>"
        ),
    }
    # MD 협의 마켓마진율 (필수)
    if gs_margin_rate:
        data["supMgnRt"] = gs_margin_rate
    return data


class GsShopPlugin(MarketPlugin):
    market_type = "gsshop"
    policy_key = "GS샵"
    required_fields = ["name", "sale_price"]

    def transform(self, product: dict, category_id: str, **kwargs) -> dict:
        """상품 데이터 → GS샵 API 포맷 변환."""
        gs_margin_rate = kwargs.get("gs_margin_rate", 0)
        return _transform_for_gsshop(product, category_id, gs_margin_rate)

    async def execute(
        self,
        session,
        product: dict,
        creds: dict,
        category_id: str,
        account,
        existing_no: str,
    ) -> dict[str, Any]:
        """GS샵 상품 등록 — 전체 로직."""
        from backend.domain.samba.proxy.gsshop import GsShopClient

        # creds가 비었으면 settings에서 조회.
        # (2026-05-25) store_gsshop 직접 호출 → resolver 위임 + account.tenant_id 자동 추출.
        auth_creds = dict(creds) if creds else {}
        if not auth_creds:
            auth_creds = await _get_setting(session, "gsshop_credentials") or {}
        if not auth_creds or not isinstance(auth_creds, dict):
            from backend.domain.samba.account.resolver import resolve_market_creds

            _tid = getattr(account, "tenant_id", None) if account else None
            auth_creds = (
                await resolve_market_creds(
                    session, _tid, market_type="gsshop", store_key="store_gsshop"
                )
                or {}
            )
        # account의 additional_fields에서 fallback
        if (not auth_creds or not isinstance(auth_creds, dict)) and account:
            extra = getattr(account, "additional_fields", None) or {}
            if (
                extra.get("supCd")
                or extra.get("aesKey")
                or extra.get("apiKeyProd")
                or extra.get("apiKeyDev")
            ):
                auth_creds = extra
        if not auth_creds or not isinstance(auth_creds, dict):
            return {"success": False, "message": "GS샵 설정이 없습니다."}

        sup_cd = (
            auth_creds.get("supCd", "")
            or auth_creds.get("storeId", "")
            or auth_creds.get("vendorId", "")
        )
        # account.seller_id fallback (계정에 supCd가 seller_id로 저장된 경우)
        if not sup_cd and account:
            sup_cd = getattr(account, "seller_id", "") or ""
        aes_key = (
            auth_creds.get("aesKey", "")
            or auth_creds.get("apiKeyProd", "")
            or auth_creds.get("apiKeyDev", "")
        )
        sub_sup_cd = auth_creds.get("subSupCd", "")
        env = "prod" if auth_creds.get("apiKeyProd") else auth_creds.get("env", "dev")

        # 정책에서 GS샵 마켓마진율 조회
        gs_margin_rate = 0
        policy_id = product.get("applied_policy_id")
        if policy_id:
            from backend.domain.samba.policy.repository import SambaPolicyRepository

            policy_repo = SambaPolicyRepository(session)
            policy = await policy_repo.get_async(policy_id)
            if policy and policy.market_policies:
                gs_policy = policy.market_policies.get("GS샵", {})
                gs_margin_rate = gs_policy.get("gsMarginRate", 0)

        client = GsShopClient(sup_cd, aes_key, sub_sup_cd, env)
        goods_data = _transform_for_gsshop(product, category_id, gs_margin_rate)
        result = await client.register_goods(goods_data)

        # GS샵 API 응답 검증 — HTTP 200이지만 본문에 fail/401 포함 가능
        data = result.get("data", {})
        if isinstance(data, dict):
            # result: "fail" 체크 (GS샵은 HTTP 200 + body에 에러 반환)
            if data.get("result") == "fail":
                msg = data.get("message", "") or data.get("code", "") or "등록 실패"
                return {
                    "success": False,
                    "message": f"GS샵 등록 실패: {msg}",
                    "data": data,
                }
            result_code = data.get("resultCode", "")
            if result_code and result_code != "00" and result_code != "SUCCESS":
                msg = (
                    data.get("resultMessage", "")
                    or data.get("message", "")
                    or f"resultCode={result_code}"
                )
                return {
                    "success": False,
                    "message": f"GS샵 등록 실패: {msg}",
                    "data": data,
                }

        return {"success": True, "message": "GS샵 등록 성공", "data": result}
