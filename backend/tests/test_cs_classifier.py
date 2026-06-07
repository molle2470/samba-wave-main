"""CS 의도 분류기 + 자동전송 게이트 안전성 단위 테스트.

프로덕션 누적 162건 검증(2026-06-08)에서 확인된 동작을 회귀 고정:
  - notice_ack(마켓 공지)만 auto_send_eligible
  - 고객 질문(재고/배송/환불)은 절대 auto_send_eligible 아님 (고객 대면 안전)
"""

from backend.domain.samba.cs_inquiry.classifier import (
    AUTO_SEND_INTENTS,
    classify,
)


def test_auto_send_intents_is_notice_ack_only():
    """자동전송 허용 의도는 notice_ack 뿐 — 범위 확장 시 의도적 검토 강제."""
    assert AUTO_SEND_INTENTS == frozenset({"notice_ack"})


def test_platform_notice_is_auto_send_eligible():
    """11번가 셀러톡 공지 = notice_ack, 자동전송 후보."""
    c = classify(
        "안녕하세요. 11번가입니다.\n\n금일 발송예정일인 주문의 미발송률이 30% 이상으로 "
        "확인되어 안내드립니다.",
        inquiry_type="urgent_inquiry",
        market="11번가",
    )
    assert c.intent == "notice_ack"
    assert c.auto_send_eligible is True


def test_urgent_inquiry_type_triggers_notice_ack():
    """urgent_inquiry 타입은 공지로 분류(데이터상 17/17 공지)."""
    c = classify(
        "판매자 평점 경고 안내", inquiry_type="urgent_inquiry", market="11번가"
    )
    assert c.intent == "notice_ack"
    assert c.auto_send_eligible is True


def test_stock_question_inside_platform_notice_is_not_auto():
    """플랫폼 인트로라도 고객 질문 신호(재고+물음표)면 자동전송 금지."""
    c = classify(
        "안녕하세요 11번가입니다. 혹시 이 상품 재고 있나요?",
        inquiry_type="general",
        market="11번가",
    )
    assert c.auto_send_eligible is False


def test_customer_questions_never_auto_send():
    """실제 고객 문의는 의도 무관하게 절대 자동전송 후보 아님 (고객 대면 안전)."""
    samples = [
        ("재고있나요?", "product"),
        ("내일 오나요?", "qna"),
        ("환불처리가 언제 되나요?", "general"),
        ("반품요청드린것 취소합니다", "general"),
        ("75가 라지사이즈인지 궁금해요", "product"),
        ("주소 변경 부탁드려요", "general"),
        ("[배송문의] 송장번호 알려주세요", "qna"),
    ]
    for content, itype in samples:
        c = classify(content, inquiry_type=itype, market="스마트스토어")
        assert c.auto_send_eligible is False, (
            f"고객질문이 auto 후보로 오분류: {content}"
        )


def test_intent_routing_basics():
    """기본 의도 라우팅 — 키워드 매칭."""
    cases = [
        ("재고있나요?", "stock_check"),
        ("반품하고싶어요", "exchange_return"),
        ("환불 언제되나요", "refund_status"),
        ("사이즈 어떤거 골라야하나요", "sizing"),
        ("송장번호 알려주세요", "tracking"),
        ("배송 언제 오나요", "delivery_eta"),
    ]
    for content, expected in cases:
        c = classify(content, inquiry_type="general", market="스마트스토어")
        assert c.intent == expected, f"{content} → {c.intent} (기대 {expected})"


def test_unmatched_is_general_low_confidence():
    """미매칭 = general + 낮은 신뢰도 + 비자동."""
    c = classify("ㅁㄴㅇㄹ", inquiry_type="general", market="스마트스토어")
    assert c.intent == "general"
    assert c.auto_send_eligible is False
    assert c.confidence <= 0.5
