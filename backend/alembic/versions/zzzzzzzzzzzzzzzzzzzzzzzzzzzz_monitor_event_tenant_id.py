"""samba_monitor_event 테이블에 tenant_id 컬럼 추가 + 운영자 backfill

격리 누수 차단 — 모니터링 이벤트가 모든 사용자에게 노출되던 문제 해결.
신규 컬럼은 nullable, 인덱스 추가. 기존 NULL 레코드는 운영자 tenant로 backfill.

idempotent — IF NOT EXISTS 패턴 + 인덱스 IF NOT EXISTS.

Revision ID: zzzzzzzzzzzzzzzzzzzzzzzzzzzz_monitor_event_tenant_id
Revises: zzzzzzzzzzzzzzzzzzzzzzzzzzz_ssg_shipment_id_trim
Create Date: 2026-05-19
"""

from typing import Sequence, Union

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
    # idempotent — IF NOT EXISTS 패턴
    op.execute(
        "ALTER TABLE samba_monitor_event "
        "ADD COLUMN IF NOT EXISTS tenant_id VARCHAR NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_samba_monitor_event_tenant_id "
        "ON samba_monitor_event (tenant_id)"
    )
    # 운영자 backfill — 기존 NULL 레코드는 운영자 tenant로
    op.execute(
        f"UPDATE samba_monitor_event SET tenant_id = '{_OWNER_TENANT_ID}' "
        "WHERE tenant_id IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_samba_monitor_event_tenant_id")
    op.execute("ALTER TABLE samba_monitor_event DROP COLUMN IF EXISTS tenant_id")
