"""samba_monitor_event 테이블에 tenant_id 컬럼 추가 + 운영자 backfill

격리 누수 차단 — 모니터링 이벤트가 모든 사용자에게 노출되던 문제 해결.
신규 컬럼은 nullable, 인덱스 추가. 기존 NULL 레코드는 운영자 tenant로 backfill.

idempotent — information_schema 체크로 ALTER 자체를 스킵 (hot 테이블 데드락 방지).

Revision ID: zzzzzzzzzzzzzzzzzzzzzzzzzzzz_monitor_event_tenant_id
Revises: zzzzzzzzzzzzzzzzzzzzzzzzzzz_ssg_shipment_id_trim
Create Date: 2026-05-19
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "zzzzzzzzzzzzzzzzzzzzzzzzzzzz_monitor_event_tenant_id"
down_revision: Union[str, Sequence[str], None] = (
    "zzzzzzzzzzzzzzzzzzzzzzzzzzz_ssg_shipment_id_trim"
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# 운영자 본인 tenant id — backfill 대상
_OWNER_TENANT_ID = "tn_01KRX6H1Q97JGPXRPB011985QT"


def upgrade() -> None:
    # hot 테이블 ALTER 데드락 방지 — IF NOT EXISTS 도 AccessExclusiveLock을 잠시
    # 잡기 때문에 idle in transaction 이 있으면 LockNotAvailable 발생.
    # information_schema/pg_indexes 로 먼저 확인하고, 있으면 ALTER 자체를 스킵.
    conn = op.get_bind()

    col_exists = conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name='samba_monitor_event' AND column_name='tenant_id'"
        )
    ).first()
    if not col_exists:
        op.execute(
            "ALTER TABLE samba_monitor_event "
            "ADD COLUMN IF NOT EXISTS tenant_id VARCHAR NULL"
        )

    idx_exists = conn.execute(
        sa.text(
            "SELECT 1 FROM pg_indexes "
            "WHERE tablename='samba_monitor_event' "
            "AND indexname='ix_samba_monitor_event_tenant_id'"
        )
    ).first()
    if not idx_exists:
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_samba_monitor_event_tenant_id "
            "ON samba_monitor_event (tenant_id)"
        )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_samba_monitor_event_tenant_id")
    op.execute("ALTER TABLE samba_monitor_event DROP COLUMN IF EXISTS tenant_id")
