"""samba_extension_key 테이블에 device_id 컬럼 추가

오토튠 status API의 _is_mine 본인 device 매칭용. 누락 시 _my_devices 빈
집합이 되어 "오토튠 정지"로 잘못 표시되던 사고 방지(2026-05-20).

idempotent — information_schema 체크로 ALTER 자체를 스킵.

Revision ID: zzzzzzzzzzzzzzzzzzzzzzzzzzzzz_extension_key_device_id
Revises: zzzzzzzzzzzzzzzzzzzzzzzzzzzz_monitor_event_tenant_id
Create Date: 2026-05-20
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "zzzzzzzzzzzzzzzzzzzzzzzzzzzzz_extension_key_device_id"
down_revision: Union[str, Sequence[str], None] = (
    "zzzzzzzzzzzzzzzzzzzzzzzzzzzz_monitor_event_tenant_id"
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    exists = conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name='samba_extension_key' AND column_name='device_id'"
        )
    ).fetchone()
    if exists:
        return
    op.add_column(
        "samba_extension_key",
        sa.Column("device_id", sa.String(length=80), nullable=True),
    )
    op.create_index(
        "ix_samba_extension_key_device_id",
        "samba_extension_key",
        ["device_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_samba_extension_key_device_id",
        table_name="samba_extension_key",
    )
    op.drop_column("samba_extension_key", "device_id")
