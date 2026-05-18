"""SambaWave 모니터링 이벤트 모델."""

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import String
from sqlmodel import Column, DateTime, Field, JSON, SQLModel, Text

from ulid import ULID


def generate_monitor_event_id() -> str:
    return f"me_{ULID()}"


class SambaMonitorEvent(SQLModel, table=True):
    """모니터링 이벤트 로그 테이블."""

    __tablename__ = "samba_monitor_event"

    id: str = Field(
        default_factory=generate_monitor_event_id,
        primary_key=True,
        max_length=30,
    )

    # 테넌트 격리
    tenant_id: Optional[str] = Field(
        default=None, sa_column=Column(String, index=True, nullable=True)
    )

    # 이벤트 분류
    event_type: str = Field(
        sa_column=Column(Text, nullable=False, index=True),
    )
    severity: str = Field(
        default="info",
        sa_column=Column(Text, nullable=False),
    )

    # 소싱처/마켓 정보
    source_site: Optional[str] = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )
    market_type: Optional[str] = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )

    # 관련 상품
    product_id: Optional[str] = Field(
        default=None,
        sa_column=Column(Text, nullable=True, index=True),
    )
    product_name: Optional[str] = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )

    # 이벤트 내용
    summary: str = Field(
        sa_column=Column(Text, nullable=False),
    )
    detail: Optional[Any] = Field(
        default=None,
        sa_column=Column(JSON, nullable=True),
    )

    # 타임스탬프
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False, index=True),
        default_factory=lambda: datetime.now(tz=timezone.utc),
    )
