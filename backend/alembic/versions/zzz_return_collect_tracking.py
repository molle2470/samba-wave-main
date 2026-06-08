"""반품 회수송장 컬럼 추가 (롯데ON 회수조회 자동수집)

Revision ID: zzz_return_collect_001
Revises: zz_cs_auto_draft_001
Create Date: 2026-06-08

소싱처 회수조회에서 자동 수집한 반품 회수송장을 저장 — CS 답변용.
  samba_order.return_collect_courier / return_collect_tracking / return_collect_at
  samba_tracking_sync_job.is_return (True면 마켓 전송 안 하고 저장만)

모두 IF NOT EXISTS — idempotent. (lifecycle._apply_startup_schema_fixes 가 매 startup 보장)
"""

from typing import Sequence, Union

from alembic import op


revision: str = "zzz_return_collect_001"
down_revision: Union[str, Sequence[str], None] = "zz_cs_auto_draft_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # samba_order 는 hot 테이블 — 컬럼 이미 있으면 ALTER 자체 스킵해 AccessExclusiveLock
    #   데드락 회피(2026-05-15 패턴). 없을 때만 ADD COLUMN IF NOT EXISTS 실행.
    from sqlalchemy import text as _sa_text

    conn = op.get_bind()

    def _exists(table: str, col: str) -> bool:
        r = conn.execute(
            _sa_text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name=:t AND column_name=:c"
            ),
            {"t": table, "c": col},
        ).first()
        return r is not None

    if not _exists("samba_order", "return_collect_courier"):
        op.execute(
            "ALTER TABLE samba_order ADD COLUMN IF NOT EXISTS "
            "return_collect_courier TEXT"
        )
    if not _exists("samba_order", "return_collect_tracking"):
        op.execute(
            "ALTER TABLE samba_order ADD COLUMN IF NOT EXISTS "
            "return_collect_tracking TEXT"
        )
    if not _exists("samba_order", "return_collect_at"):
        op.execute(
            "ALTER TABLE samba_order ADD COLUMN IF NOT EXISTS "
            "return_collect_at TIMESTAMP WITH TIME ZONE"
        )
    if not _exists("samba_tracking_sync_job", "is_return"):
        op.execute(
            "ALTER TABLE samba_tracking_sync_job ADD COLUMN IF NOT EXISTS "
            "is_return BOOLEAN NOT NULL DEFAULT false"
        )


def downgrade() -> None:
    pass
