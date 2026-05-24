"""GS샵 재고변동 감지 회귀 테스트.

배경: GS샵은 품절되면 상세 페이지에서 옵션(attrTypList)이 통째로 사라진다.
옵션 0개면 count_stock_transitions 가 셀 대상이 없어 항상 0을 반환 →
전체품절인데도 stock_changed=False 가 되어 '재고변동' 기록이 누락되는 사고 발생
(프로덕션 실측: 품절 상품 3건이 stock_changed=False).

수정: 옵션 유무와 무관하게 in_stock↔sold_out 전환 자체를 재고변동으로 인정.
"""

import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.domain.samba.plugins.sourcing.gsshop import GsShopSourcingPlugin


class _FakeProduct:
    def __init__(self, sale_status: str, options: list | None) -> None:
        self.id = "col_gsshop_test"
        self.site_product_id = "1109875187"
        self.name = "테스트 상품"
        self.sale_status = sale_status
        self.sale_price = 65000
        self.cost = 50000
        self.options = options or []


def _run_refresh(product: _FakeProduct, detail: dict, monkeypatch) -> object:
    """get_product_detail 를 모킹하고 plugin.refresh 실행."""

    async def _fake_detail(self, product_id, refresh_only=False):  # noqa: ANN001
        return detail

    # refresh 내부에서 import 하는 클라이언트 클래스에 직접 패치
    from backend.domain.samba.proxy import gsshop_sourcing

    monkeypatch.setattr(
        gsshop_sourcing.GsShopSourcingClient,
        "get_product_detail",
        _fake_detail,
        raising=True,
    )

    plugin = GsShopSourcingPlugin()
    return asyncio.run(plugin.refresh(product))


class TestGsShopStockChanged:
    def test_soldout_with_empty_options_marks_stock_changed(self, monkeypatch) -> None:
        # 핵심 회귀: 품절 + 옵션 0개 → 옵션 못 세도 상태 전환으로 stock_changed=True
        product = _FakeProduct("in_stock", [{"name": "FREE", "stock": 99}])
        detail = {
            "salePrice": 49000,
            "originalPrice": 49000,
            "isOutOfStock": True,
            "options": [],
        }
        r = _run_refresh(product, detail, monkeypatch)
        assert r.new_sale_status == "sold_out"
        assert r.stock_changed is True

    def test_restock_from_soldout_marks_stock_changed(self, monkeypatch) -> None:
        # 반대 방향: 품절 → 재입고 전환도 재고변동
        product = _FakeProduct("sold_out", [])
        detail = {
            "salePrice": 49000,
            "isOutOfStock": False,
            "options": [{"name": "FREE", "isSoldOut": False, "stock": 99}],
        }
        r = _run_refresh(product, detail, monkeypatch)
        assert r.new_sale_status == "in_stock"
        assert r.stock_changed is True

    def test_instock_unchanged_no_stock_changed(self, monkeypatch) -> None:
        # 변동 없음(계속 판매중) → 재고변동 아님
        product = _FakeProduct(
            "in_stock", [{"name": "FREE", "isSoldOut": False, "stock": 99}]
        )
        detail = {
            "salePrice": 49000,
            "isOutOfStock": False,
            "options": [{"name": "FREE", "isSoldOut": False, "stock": 99}],
        }
        r = _run_refresh(product, detail, monkeypatch)
        assert r.new_sale_status == "in_stock"
        assert r.stock_changed is False

    def test_option_level_transition_still_detected(self, monkeypatch) -> None:
        # 옵션이 있는 경우 기존 count_stock_transitions 경로도 그대로 동작
        product = _FakeProduct(
            "in_stock",
            [
                {"name": "S", "isSoldOut": False, "stock": 99},
                {"name": "M", "isSoldOut": False, "stock": 99},
            ],
        )
        detail = {
            "salePrice": 41250,
            "isOutOfStock": True,
            "options": [
                {"name": "S", "isSoldOut": True},
                {"name": "M", "isSoldOut": True},
            ],
        }
        r = _run_refresh(product, detail, monkeypatch)
        assert r.new_sale_status == "sold_out"
        assert r.stock_changed is True
