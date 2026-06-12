"""SambaWave Forbidden domain models - forbidden words and global settings."""

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import Boolean, String
from sqlmodel import Column, DateTime, Field, JSON, SQLModel, Text

from ulid import ULID


def generate_forbidden_word_id() -> str:
    return f"fw_{ULID()}"


class SambaForbiddenWord(SQLModel, table=True):
    """금칙어 테이블 - 상품명/설명에서 필터링할 단어."""

    __tablename__ = "samba_forbidden_word"

    id: str = Field(
        default_factory=generate_forbidden_word_id,
        primary_key=True,
        max_length=30,
    )
    # 테넌트 격리
    tenant_id: Optional[str] = Field(
        default=None, sa_column=Column(String, index=True, nullable=True)
    )

    word: str = Field(sa_column=Column(Text, nullable=False))

    # 유형: 'forbidden' (치환/삭제 대상) | 'deletion' (상품 제외 대상)
    type: str = Field(
        sa_column=Column(Text, nullable=False, index=True),
    )

    # 적용 범위: 'title' | 'description' | 'both'
    scope: str = Field(
        default="title",
        sa_column=Column(Text, nullable=False),
    )

    group_id: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))

    # 마켓 한정: None = 공통(모든 마켓), 'smartstore'/'coupang'/'11st' 등 = 해당 마켓 전용
    # 적용 시 '공통 + 해당 마켓' 합산(additive). markets.ts 의 마켓 id 와 동일 문자열.
    market: Optional[str] = Field(
        default=None, sa_column=Column(String, index=True, nullable=True)
    )

    is_active: bool = Field(
        default=True, sa_column=Column(Boolean, nullable=False, server_default="true")
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


class SambaSettings(SQLModel, table=True):
    """글로벌 설정 테이블 - key-value 기반 시스템 설정."""

    __tablename__ = "samba_settings"

    key: str = Field(primary_key=True)
    # 테넌트 격리
    tenant_id: Optional[str] = Field(
        default=None, sa_column=Column(String, index=True, nullable=True)
    )

    # 설정 값 (JSON)
    value: Optional[Any] = Field(default=None, sa_column=Column(JSON, nullable=True))

    # Timestamp
    updated_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
        default_factory=lambda: datetime.now(tz=timezone.utc),
    )
