"""롯데홈 goods_no 게이트 동작(behavioral) 테스트.

정적 문자열 검사(test_lottehome_goods_no_hard_gate_static.py)와 달리, 게이트
판정 함수를 실제로 호출해 분기를 검증한다. 특히 price/stock 전용 업데이트
(오토튠)가 false-failure 로 막히지 않는지(회귀 방지)를 고정한다.
"""

from backend.domain.samba.shipment.service import _lottehome_goods_no_gate


def test_new_registration_with_goods_no_passes():
    ok, goods_no = _lottehome_goods_no_gate(
        {"success": True, "goodsNo": "12345", "product_no": "12345"},
        product_no="",
        is_price_stock_only=False,
    )
    assert ok is True
    assert goods_no == "12345"


def test_new_registration_without_goods_no_is_blocked():
    # 신규등록 성공 응답인데 goods_no 가 전혀 없으면 성공 취소(미연결 라이브 차단).
    ok, goods_no = _lottehome_goods_no_gate(
        {"success": True, "updated": []},
        product_no="",
        is_price_stock_only=False,
    )
    assert ok is False
    assert goods_no == ""


def test_new_registration_with_zero_goods_no_is_blocked():
    # "0"/"0.0" 은 무효 상품번호 — 차단해야 한다.
    for bad in ("0", "0.0"):
        ok, _ = _lottehome_goods_no_gate(
            {"success": True, "goodsNo": bad},
            product_no="",
            is_price_stock_only=False,
        )
        assert ok is False, f"goods_no={bad!r} 는 차단되어야 함"


def test_extract_fallback_product_no_recovers_goods_no():
    # 응답 본문엔 키가 없지만 _extract_market_product_no 가 복구한 product_no 가
    # 있으면 그것으로 통과한다.
    ok, goods_no = _lottehome_goods_no_gate(
        {"success": True},
        product_no="98765",
        is_price_stock_only=False,
    )
    assert ok is True
    assert goods_no == "98765"


def test_price_stock_only_update_without_goods_no_passes():
    # 회귀 방지: 오토튠 가격/재고 전용 업데이트는 새 goods_no 를 발급하지 않으므로
    # 게이트를 면제한다. 면제 시 goods_no 는 빈 문자열이라 호출부가 기존 product_no
    # 처리를 그대로 두게 된다(= 변경 전 동작).
    ok, goods_no = _lottehome_goods_no_gate(
        {"success": True, "updated": ["price"]},
        product_no="",
        is_price_stock_only=True,
    )
    assert ok is True
    assert goods_no == ""


def test_price_stock_only_update_keeps_existing_product_no():
    ok, goods_no = _lottehome_goods_no_gate(
        {"success": True, "updated": ["price", "stock"]},
        product_no="55501",
        is_price_stock_only=True,
    )
    assert ok is True
    assert goods_no == "55501"
