"""SambaWave Return domain model."""

from datetime import datetime, timezone
from typing import Any, List, Optional

from sqlmodel import Column, DateTime, Field, JSON, SQLModel, String, Text

from ulid import ULID


def generate_return_id() -> str:
    return f"ret_{ULID()}"


class SambaReturn(SQLModel, table=True):
    """반품/교환/취소 테이블."""

    __tablename__ = "samba_return"

    id: str = Field(
        default_factory=generate_return_id,
        primary_key=True,
        max_length=30,
    )

    # 테넌트 격리
    tenant_id: Optional[str] = Field(
        default=None, sa_column=Column(String, index=True, nullable=True)
    )

    # 연결 주문
    order_id: str = Field(
        sa_column=Column(Text, nullable=False, index=True),
    )

    # 주문번호 (마켓 상품주문번호 — 사용자에게 표시되는 번호)
    order_number: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )

    # 상품 이미지
    product_image: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )

    # 상품명
    product_name: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )

    # 고객명
    customer_name: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )

    # 사업자명 (마켓 계정명)
    business_name: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )

    # 판매 마켓
    market: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))

    # 고객 전화번호
    customer_phone: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )

    # 확인 여부
    confirmed: bool = Field(default=False)

    # 주문일
    order_date: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )

    # 체크날짜
    check_date: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )

    # 정산금액
    settlement_amount: Optional[float] = Field(default=None)

    # 환수금액
    recovery_amount: Optional[float] = Field(default=None)

    # 고객 (수기 입력 — 자유 텍스트)
    customer_amount: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )

    # 회사 (수기 입력 — 자유 텍스트)
    company_amount: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )

    # 반품링크 (수기 입력 — 자동 계산 return_link와 별개로 영구 저장)
    return_link_manual: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )

    # 고객전화번호 (수기 입력 — 마켓 customer_phone과 별개로 영구 저장.
    # 마켓 번호가 안심번호일 때 직접 덮어쓰기용. 재동기화에 덮이지 않음)
    customer_phone_manual: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )

    # 소싱주문번호 (수기 입력 — 마켓 주문번호와 별개로 직접 입력·영구 저장)
    sourcing_order_no: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )

    # 메모
    memo: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))

    # 상품위치 (배송지 시/군)
    product_location: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )

    # 전체 배송 주소
    customer_address: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )

    # 원문링크 (소싱처 상품 URL)
    return_link: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )

    # 반품신청한곳 (소싱처명)
    return_source: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )

    # 지역
    region: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))

    # 반품요청일
    return_request_date: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )

    # 마켓 주문상태 (교환요청, 취소완료 등)
    market_order_status: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )

    # 완료내역 (진행중, 취소, 교환, 반품)
    completion_detail: Optional[str] = Field(
        default="진행중", sa_column=Column(Text, nullable=True)
    )

    # 유형: return, exchange, cancel
    type: str = Field(sa_column=Column(Text, nullable=False))

    # 사유
    reason: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))

    # 상세 설명
    description: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )

    # 수량
    quantity: int = Field(default=1)

    # 환불 요청 금액
    requested_amount: Optional[float] = Field(default=None)

    # 상태: requested, approved, rejected, completed, cancelled
    status: str = Field(
        default="requested",
        sa_column=Column(Text, nullable=False, index=True),
    )

    # 승인/완료 일시
    approval_date: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    completion_date: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )

    # 고객주문 반품 상태
    customer_order_no: Optional[str] = Field(
        default="return_incomplete", sa_column=Column(Text, nullable=True)
    )

    # 원주문 반품 상태
    original_order_no: Optional[str] = Field(
        default="return_incomplete", sa_column=Column(Text, nullable=True)
    )

    # 메모 [{date, message}]
    notes: Optional[List[Any]] = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )

    # 타임라인 [{date, status, message}]
    timeline: Optional[List[Any]] = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )

    # ── 교환 전용 필드 ──────────────────────────────────────────────
    # 11번가 클레임 요청번호 (교환/반품 공용 — API 제공)
    clm_req_seq: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )

    # 11번가 주문상품순번 (교환 승인/거부 API 호출에 필요 — API 제공)
    ord_prd_seq: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )

    # 교환 상품 회수 상태 (수기 입력: 미회수 / 회수중 / 회수완료)
    exchange_retrieval_status: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )

    # 교환 상품 회수 완료 일자 (수기 입력)
    exchange_retrieved_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )

    # 소싱처(구매처)에서 고객에게 출고한 택배사 (수기 입력)
    exchange_reship_company: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )

    # 소싱처(구매처)에서 고객에게 출고한 송장번호 (수기 입력)
    exchange_reship_tracking: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )

    # 교환 상품 고객 도착 예정/확인 일자 (수기 입력)
    exchange_delivered_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )

    # Timestamps
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
        default_factory=lambda: datetime.now(tz=timezone.utc),
    )
    updated_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
        default_factory=lambda: datetime.now(tz=timezone.utc),
    )
