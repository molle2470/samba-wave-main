"""패션플러스 소싱처 플러그인."""

import logging
from typing import TYPE_CHECKING

from backend.domain.samba.plugins.sourcing_base import SourcingPlugin

if TYPE_CHECKING:
    from backend.domain.samba.collector.refresher import RefreshResult

logger = logging.getLogger(__name__)


class FashionPlusPlugin(SourcingPlugin):
    """패션플러스 소싱처 플러그인.

    concurrency=3: 동시 3개 요청
    request_interval=0.5: 요청 간 500ms 딜레이
    """

    site_name = "FASHIONPLUS"
    concurrency = 3
    request_interval = 0.5

    async def discover_brands(self, keyword: str) -> dict:
        """패션플러스 브랜드 탐색."""
        from backend.domain.samba.proxy.fashionplus import FashionPlusClient

        client = FashionPlusClient()
        return await self.safe_call(client.discover_brands(keyword))

    async def scan_categories(
        self, keyword: str, selected_brands: list[str] | None = None
    ) -> dict:
        """패션플러스 카테고리 스캔."""
        from backend.domain.samba.proxy.fashionplus import FashionPlusClient

        client = FashionPlusClient()
        return await self.safe_call(
            client.scan_categories(keyword, selected_brands=selected_brands)
        )

    async def search(self, keyword: str, **filters) -> list[dict]:
        """패션플러스 키워드 검색."""
        from backend.domain.samba.proxy.fashionplus import FashionPlusClient

        max_count = int(filters.get("max_count", 100))
        client = FashionPlusClient()
        result = await self.safe_call(client.search(keyword, max_count=max_count))
        return result.get("products", [])

    async def get_detail(self, site_product_id: str) -> dict:
        """패션플러스 상품 상세 조회."""
        from backend.domain.samba.proxy.fashionplus import FashionPlusClient

        client = FashionPlusClient()
        return await self.safe_call(client.get_detail(site_product_id))

    async def refresh(self, product) -> "RefreshResult":
        """가격/재고 갱신 — FashionPlusClient로 재조회 후 변경분 반환."""
        from backend.domain.samba.collector.refresher import RefreshResult
        from backend.domain.samba.proxy.fashionplus import FashionPlusClient

        product_id = getattr(product, "id", "")
        site_product_id = getattr(product, "site_product_id", "")

        if not site_product_id:
            return RefreshResult(product_id=product_id, error="site_product_id 없음")

        try:
            client = FashionPlusClient()
            fresh = await client.get_detail(site_product_id)
        except Exception as e:
            logger.warning(f"[FashionPlus] 갱신 실패 {site_product_id}: {e}")
            return RefreshResult(product_id=product_id, error=str(e))

        if not fresh or not fresh.get("name"):
            return RefreshResult(product_id=product_id, error="상세 조회 실패")

        new_sale_price = fresh.get("sale_price")
        new_original_price = fresh.get("original_price")
        # 배송비 포함 원가 — sale_price 추출 실패 시 cost=None (배송비만 박는 폴백 금지)
        shipping_fee = fresh.get("shipping_fee", 3000)
        if new_sale_price is None or new_sale_price <= 0:
            new_cost = None
            _price_uncertain = True
            logger.warning(
                f"[FashionPlus][가격불확실] sale_price 추출 실패: {site_product_id} "
                f"→ cost 갱신 및 전송 보류"
            )
        else:
            new_cost = new_sale_price + shipping_fee
            _price_uncertain = False

        old_sale_price = getattr(product, "sale_price", None)
        old_cost = getattr(product, "cost", None)

        price_changed = (
            new_sale_price is not None
            and new_sale_price != old_sale_price
            or new_cost != old_cost
        )

        # 옵션 재고 갱신
        new_options = fresh.get("options")
        stock_changed = False
        new_sale_status = "in_stock"
        if new_options is not None:
            all_sold_out = all(
                o.get("isSoldOut", False) or o.get("stock", 0) == 0 for o in new_options
            )
            if all_sold_out and len(new_options) > 0:
                new_sale_status = "sold_out"
                stock_changed = True

        return RefreshResult(
            product_id=product_id,
            new_sale_price=new_sale_price,
            new_original_price=new_original_price,
            new_cost=new_cost,
            new_sale_status=new_sale_status,
            new_options=new_options,
            changed=price_changed,
            stock_changed=stock_changed,
            price_uncertain=_price_uncertain,
        )
