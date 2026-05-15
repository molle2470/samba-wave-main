"""samba_daily_registered_snapshot 테이블 추가

배경:
- 대시보드 "최근 일주일 매출" 등록상품수 칼럼이 두 가지 자(尺)를 섞어 산식이 어긋남
  - 오늘: registered_accounts 현재값 (정답)
  - 어제까지: first_market_registered_at/fully_unregistered_at 누적 (부정확)
- 매일 0시 크론이 그날의 "지금 마켓에 살아있는 상품수"를 한 줄씩 저장 → 자(尺) 통일

idempotent — IF NOT EXISTS raw SQL (feedback_migration_idempotent.md 준수).
"""

from collections.abc import Sequence
from typing import Union

from alembic import op


revision: str = "zzzzzzzzzzzzzzzzzzzz_add_daily_registered_snapshot"
down_revision: Union[str, Sequence[str], None] = (
    "zzzzzzzzzzzzzzzzzzz_add_tracking_sync_job"
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS samba_daily_registered_snapshot (
            snapshot_date VARCHAR(10) PRIMARY KEY,
            registered_count INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS samba_daily_registered_snapshot")
