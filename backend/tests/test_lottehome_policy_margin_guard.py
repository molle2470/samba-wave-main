from backend.domain.samba.shipment.service import (
    _validate_lottehome_policy_margin_price,
    calc_market_price,
)


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


def test_lottehome_margin_rate_is_commission_not_policy_margin():
    # 롯데홈 market_policy.marginRate 는 API mrgn_rt(위탁수수료율)이다.
    # 이를 정책마진으로 오해하거나 feeRate=0 으로 처리하면 아래 역마진 케이스가 통과한다.
    ok, detail = _validate_lottehome_policy_margin_price(
        sale_price=85000,
        cost=67670,
        policy_pricing={"marginRate": 6, "minMarginAmount": 4500, "shippingCost": 0},
        market_policy={"marginRate": 20},
        source_site="MUSINSA",
        is_point_restricted=None,
    )

    assert ok is False
    assert detail["policy_margin_rate"] == 6
    assert detail["lotte_fee_rate"] == 20
    assert detail["expected_settlement"] == 68000
    assert detail["required_settlement"] == 72170
    assert detail["shortfall"] == 4170


def test_lottehome_calc_market_price_uses_lottehome_margin_rate_as_fee():
    sale_price = calc_market_price(
        cost=67670,
        policy_pricing={"marginRate": 6, "minMarginAmount": 4500, "shippingCost": 0},
        market_type="lottehome",
        market_policies={"롯데홈쇼핑": {"marginRate": 20}},
        source_site="MUSINSA",
        is_point_restricted=None,
    )

    assert sale_price == 90300
    ok, detail = _validate_lottehome_policy_margin_price(
        sale_price=sale_price,
        cost=67670,
        policy_pricing={"marginRate": 6, "minMarginAmount": 4500, "shippingCost": 0},
        market_policy={"marginRate": 20},
        source_site="MUSINSA",
        is_point_restricted=None,
    )
    assert ok is True
    assert detail["expected_settlement"] == 72240
    assert detail["required_settlement"] == 72170


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
