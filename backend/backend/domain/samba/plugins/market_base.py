from abc import ABC, abstractmethod
from typing import Any
import logging

logger = logging.getLogger(__name__)


class MarketPlugin(ABC):
    """마켓 플러그인 기본 클래스.
    새 마켓 추가 시 execute()와 transform() 2개만 구현.
    인증 로드, 정책 주입, 에러 분류는 base가 처리.
    """

    market_type: str  # "smartstore"
    policy_key: str  # "스마트스토어"
    required_fields: list[str] = ["name", "sale_price"]

    async def handle(
        self, session, product: dict, category_id: str, account, existing_no: str = ""
    ) -> dict[str, Any]:
        """마켓 전송 진입점."""
        creds = await self._load_auth(session, account)
        if not creds:
            return {"success": False, "message": f"{self.market_type} 인증정보 없음"}
        category_id = self._validate_category(category_id)
        product = await self._apply_market_settings(session, product, account)
        if not category_id:
            return {
                "success": False,
                "message": f"{self.market_type} 카테고리 코드 없음",
            }
        # DB 읽기 완료 — HTTP API 호출 전 트랜잭션 종료 (idle in transaction 방지)
        try:
            await session.commit()
        except Exception:
            pass
        try:
            return await self.execute(
                session, product, creds, category_id, account, existing_no
            )
        except Exception as e:
            return {
                "success": False,
                "error_type": self._classify_error(e),
                "message": str(e),
            }

    def _classify_error(self, exc: Exception) -> str:
        """에러 유형 분류."""
        msg = str(exc).lower()
        if "401" in msg or "403" in msg or "token" in msg:
            return "auth_failed"
        if "timeout" in msg or "connect" in msg:
            return "network"
        if "400" in msg or "invalid" in msg:
            return "schema_changed"
        return "unknown"

    async def _load_auth(self, session, account) -> dict | None:
        """인증정보 로드 — account 우선, account 없을 때만 settings 폴백.
        account가 명시됐는데 credentials가 없으면 None 반환 (다른 계정으로 오인 전송 방지).
        """
        creds = {}
        if account:
            extras = account.additional_fields or {}
            creds = {k: v for k, v in extras.items() if v}
            # additional_fields 내용과 무관하게 컬럼값 보충 (없는 키만 추가)
            if account.api_key and "apiKey" not in creds:
                creds["apiKey"] = account.api_key
            if account.api_secret and "apiSecret" not in creds:
                creds["apiSecret"] = account.api_secret
            if account.seller_id and "sellerId" not in creds:
                creds["sellerId"] = account.seller_id
            # account 지정됐으나 credentials 없으면 폴백 없이 None 반환
            return creds or None
        # account가 None인 경우에만 SambaSettings 폴백 (레거시 단일계정)
        from backend.domain.samba.forbidden.model import SambaSettings
        from sqlmodel import select

        stmt = select(SambaSettings).where(
            SambaSettings.key == f"store_{self.market_type}"
        )
        result = await session.execute(stmt)
        row = result.scalars().first()
        if row and isinstance(row.value, dict):
            creds = row.value
        return creds or None

    def _validate_category(self, category_id: str) -> str:
        """카테고리 코드 유효성 검증."""
        if category_id and not category_id.isdigit():
            return ""
        return category_id

    async def _apply_market_settings(self, session, product: dict, account) -> dict:
        """정책에서 마켓별 설정 주입."""
        policy_id = product.get("applied_policy_id")
        if not policy_id:
            return product
        from backend.domain.samba.policy.repository import SambaPolicyRepository

        policy_repo = SambaPolicyRepository(session)
        policy = await policy_repo.get_async(policy_id)
        if policy and policy.market_policies:
            mp = policy.market_policies.get(self.policy_key, {})
            if mp.get("shippingCost"):
                product["_delivery_fee_type"] = "PAID"
                product["_delivery_base_fee"] = int(mp["shippingCost"])
            if mp.get("maxStock"):
                product["_max_stock"] = mp["maxStock"]
            # SSG 주문수량 제한 (정책 → 상품에 주입)
            if mp.get("dayMaxQty"):
                product["_day_max_qty"] = int(mp["dayMaxQty"])
            if mp.get("onceMinQty"):
                product["_once_min_qty"] = int(mp["onceMinQty"])
            if mp.get("onceMaxQty"):
                product["_once_max_qty"] = int(mp["onceMaxQty"])
        if account:
            extras = account.additional_fields or {}
            if extras.get("asPhone"):
                product["_as_phone"] = extras["asPhone"]
        return product

    @abstractmethod
    async def execute(
        self,
        session,
        product: dict,
        creds: dict,
        category_id: str,
        account,
        existing_no: str,
    ) -> dict[str, Any]:
        """마켓 API 호출 (등록/수정)."""
        ...

    @abstractmethod
    def transform(self, product: dict, category_id: str, **kwargs) -> dict:
        """상품 데이터 -> 마켓 API 포맷 변환."""
        ...

    async def delete(self, session, product_no: str, account) -> dict[str, Any]:
        """마켓 상품 삭제 — 기본은 미지원."""
        return {"success": False, "message": f"{self.market_type} 삭제 미지원"}

    async def test_auth(self, session, account) -> bool:
        """인증 테스트 — 기본은 항상 성공."""
        return True
