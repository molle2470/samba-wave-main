from backend.domain.samba.shipment.service import _validate_lottehome_policy_margin_price


def test_lottehome_policy_margin_guard_blocks_below_policy_required_settlement():
    ok, detail = _validate_lottehome_policy_margin_price(
        sale_price=68670,
        cost=67670,
        policy_pricing={"marginRate": 6, "minMarginAmount": 4500, "shippingCost": 0},
        market_policy={"feeRate": 20},
        source_site="MUSINSA",
        is_point_restricted=None,
    )

    assert ok is False
    assert detail["expected_settlement"] == 54936
    assert detail["required_settlement"] == 72170
    assert detail["policy_margin_amount"] == 4500
    assert detail["shortfall"] == 17234


def test_lottehome_policy_margin_guard_allows_policy_margin_price():
    ok, detail = _validate_lottehome_policy_margin_price(
        sale_price=90300,
        cost=67670,
        policy_pricing={"marginRate": 6, "minMarginAmount": 4500, "shippingCost": 0},
        market_policy={"feeRate": 20},
        source_site="MUSINSA",
        is_point_restricted=None,
    )

    assert ok is True
    assert detail["expected_settlement"] == 72240
    assert detail["required_settlement"] == 72170


def test_lottehome_policy_margin_guard_uses_source_site_extra_margin():
    ok, detail = _validate_lottehome_policy_margin_price(
        sale_price=90300,
        cost=67670,
        policy_pricing={
            "marginRate": 6,
            "minMarginAmount": 4500,
            "shippingCost": 0,
            "sourceSiteMargins": {"LOTTEON": {"marginAmount": 3000}},
        },
        market_policy={"feeRate": 20},
        source_site="LOTTEON",
        is_point_restricted=None,
    )

    assert ok is False
    assert detail["source_margin_amount"] == 3000
    assert detail["required_settlement"] == 75170
