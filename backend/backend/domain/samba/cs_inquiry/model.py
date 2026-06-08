"""SambaWave CS 문의 도메인 모델."""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, Float
from sqlmodel import Column, DateTime, Field, SQLModel, Text

from ulid import ULID


def generate_cs_inquiry_id() -> str:
    return f"csi_{ULID()}"


class SambaCSInquiry(SQLModel, table=True):
    """마켓 CS 문의 테이블 - 각 마켓에서 수집된 고객 문의."""

    __tablename__ = "samba_cs_inquiry"

    id: str = Field(
        default_factory=generate_cs_inquiry_id,
        primary_key=True,
        max_length=30,
    )

    # 마켓 정보
    market: str = Field(sa_column=Column(Text, nullable=False, index=True))
    market_order_id: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True, index=True)
    )
    # 마켓측 문의 번호 (스마트스토어: inquiryNo)
    market_inquiry_no: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True, index=True)
    )
    # 마켓측 답변 번호 (스마트스토어: inquiryCommentNo)
    market_answer_no: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    account_id: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True, index=True)
    )
    account_name: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )

    # SSG 외부 연동
    external_id: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True, index=True)
    )
    external_sent: bool = Field(
        default=False, sa_column=Column(Boolean, nullable=False, server_default="false")
    )

    # 문의 정보
    inquiry_type: str = Field(
        default="general",
        sa_column=Column(Text, nullable=False, index=True),
    )
    questioner: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )

    # 수집상품 연결
    collected_product_id: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True, index=True)
    )
    # 마켓측 상품번호 (스마트스토어: originProductNo, 쿠팡: productId 등)
    market_product_no: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True, index=True)
    )

    # 상품 정보
    product_name: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    product_image: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    product_link: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    market_link: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    original_link: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )

    # 문의/답변 내용
    content: str = Field(sa_column=Column(Text, nullable=False))
    reply: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))

    # 답변 상태: pending, replied
    reply_status: str = Field(
        default="pending",
        sa_column=Column(Text, nullable=False, index=True),
    )

    # 숨김 여부
    is_hidden: bool = Field(
        default=False, sa_column=Column(Boolean, nullable=False, server_default="false")
    )

    # 일시
    replied_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    inquiry_date: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    collected_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
        default_factory=lambda: datetime.now(tz=timezone.utc),
    )
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
        default_factory=lambda: datetime.now(tz=timezone.utc),
    )
    # 카페24 CS 게시판 번호 (board_no 기반 문의 조회용)
    board_no: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))

    # ==================== CS 자동화 (Tier 0~3) ====================
    # 룰기반 의도 분류 결과 (stock_check/delivery_eta/exchange_return/
    #   refund_status/sizing/notice_ack/general 등). NULL=미분류
    intent: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True, index=True)
    )
    # Claude 스케줄잡이 작성한 답변 초안 (전송 전 검토 대상)
    draft_reply: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    # 초안 상태: none/suggested/auto_sent/accepted/edited/rejected
    draft_status: str = Field(
        default="none",
        sa_column=Column(Text, nullable=False, server_default="none", index=True),
    )
    # 초안 신뢰도 0.0~1.0 — 자동전송 게이트 판정
    draft_confidence: Optional[float] = Field(
        default=None, sa_column=Column(Float, nullable=True)
    )
    # 초안 출처: claude/template/rule
    draft_source: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    # 초안 작성 일시
    drafted_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
