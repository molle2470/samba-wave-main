"""SambaWave Product domain model."""

from datetime import datetime, timezone
from typing import Any, List, Optional

from sqlalchemy import Boolean, String
from sqlmodel import Column, DateTime, Field, JSON, SQLModel, Text

from ulid import ULID


def generate_product_id() -> str:
    return f"prd_{ULID()}"


class SambaProduct(SQLModel, table=True):
    """상품 테이블 - 소싱처에서 수집/등록한 상품."""

    __tablename__ = "samba_product"

    id: str = Field(
        default_factory=generate_product_id,
        primary_key=True,
        max_length=30,
    )
    # 테넌트 격리
    tenant_id: Optional[str] = Field(
        default=None, sa_column=Column(String, index=True, nullable=True)
    )

    # 기본 정보
    name: str = Field(sa_column=Column(Text, nullable=False))
    name_en: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    name_ja: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    description: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    category: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True, index=True)
    )
    brand: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True, index=True)
    )

    # 소싱 정보
    source_url: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    source_site: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True, index=True)
    )
    site_product_id: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True, index=True)
    )

    # 가격 정보
    source_price: float = Field(default=0)
    cost: float = Field(default=0)
    # 무신사 보유 적립금 사용 제외 cost (정책 토글 excludeHeldPoint=True에서 사용)
    cost_excl_held_point: Optional[float] = Field(default=None)
    margin_rate: float = Field(default=30)
    sale_price: Optional[float] = Field(default=None)

    # 이미지/옵션 (JSON)
    images: Optional[List[str]] = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )
    options: Optional[List[Any]] = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )

    # 카테고리 계층
    category1: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    category2: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    category3: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    category4: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )

    # 상태
    status: str = Field(
        default="active",
        sa_column=Column(Text, nullable=False, index=True),
    )

    # 마켓 전송 관련 (JSON)
    market_prices: Optional[Any] = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )
    market_enabled: Optional[Any] = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )
    registered_accounts: Optional[List[str]] = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )
    applied_policy_id: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )

    # 가격 변동 추적
    price_before_change: Optional[float] = Field(default=None)
    price_changed_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    is_sold_out: bool = Field(
        default=False, sa_column=Column(Boolean, nullable=False, server_default="false")
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
