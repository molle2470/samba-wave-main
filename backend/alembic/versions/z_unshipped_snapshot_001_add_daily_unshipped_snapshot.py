"""samba_daily_unshipped_snapshot 테이블 추가

배경:
- 대시보드 "최근 일주일 매출" 미발송 칼럼이 송장 진행현황 모달 "대기"(트레일링 7일
  송장 대기수)와 다른 자(尺)를 써서 숫자가 어긋남(모달 42 vs 대시보드 9).
- 미발송 = 송장 대기수(PENDING) 로 통일하되, 모달 "대기"는 트레일링 7일 누적이라
  특정 날짜에 귀속시킬 수 없음 → 매일 0시 크론이 그날 시점의 대기수를 한 줄씩 스냅샷.
- 대시보드 오늘 행은 라이브 재계산, 과거 행은 이 스냅샷 사용.

idempotent — IF NOT EXISTS raw SQL (feedback_migration_idempotent.md 준수).
주의: 프로덕션 entrypoint.sh 는 최신 head로 stamp 후 upgrade 하므로 이 마이그레이션은
프로덕션에서 실제 실행되지 않음 → 배포 전 테이블 수동 생성 필요(아래 CREATE 동일 SQL).
"""

from collections.abc import Sequence
from typing import Union

from alembic import op


revision: str = "z_unshipped_snapshot_001"
down_revision: Union[str, Sequence[str], None] = "z_ret_phone_srcorder_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS samba_daily_unshipped_snapshot (
            snapshot_date VARCHAR(10) PRIMARY KEY,
            unshipped_count INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS samba_daily_unshipped_snapshot")
