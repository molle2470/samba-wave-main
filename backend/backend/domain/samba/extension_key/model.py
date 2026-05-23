"""확장앱 API 키 영속화 모델 — 테넌트별 키 발급/revoke."""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, Column, DateTime, Index, String, Text
from sqlmodel import Field, SQLModel

UTC = timezone.utc


class SambaExtensionKey(SQLModel, table=True):
    """확장앱 테넌트별 API 키. 평문 키는 발급 시 1회만 노출, 이후 hash 저장."""

    __tablename__ = "samba_extension_key"
    __table_args__ = (
        Index(
            "ix_samba_extension_key_active",
            "tenant_id",
            "revoked_at",
            postgresql_where="revoked_at IS NULL",
        ),
        Index("ix_samba_extension_key_key_hash", "key_hash", unique=True),
    )

    id: str = Field(sa_column=Column(String(40), primary_key=True))
    key_hash: str = Field(sa_column=Column(String(128), nullable=False))
    tenant_id: Optional[str] = Field(
        default=None, sa_column=Column(String(40), nullable=True, index=True)
    )
    user_id: str = Field(sa_column=Column(String(40), nullable=False, index=True))
    label: Optional[str] = Field(
        default=None, sa_column=Column(String(80), nullable=True)
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    last_used_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    expires_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    revoked_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    note: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    # 확장앱이 키 발급 시 보낸 X-Device-Id (collector_autotune.autotune_status 본인 device 매칭용)
    # 2026-05-20 추가 — 누락 시 _is_mine 매핑 실패로 "오토튠 정지" 오표시.
    device_id: Optional[str] = Field(
        default=None, sa_column=Column(String(80), nullable=True, index=True)
    )
    # install-token 여부 (2026-05-23) — 데몬 다운로드 시 발급하는 1시간 만료 단기 토큰.
    # 데몬 첫 실행 시 /extension-keys/exchange 로 long-lived 키와 교환 후 즉시 revoke.
    # is_install_token=True 인 키는 api_gateway 일반 인증에서 거부, exchange 전용.
    is_install_token: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default="false"),
    )
