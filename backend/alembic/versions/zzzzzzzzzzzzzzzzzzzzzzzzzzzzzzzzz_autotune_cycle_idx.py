"""오토튠 사이클 SELECT 전용 인덱스 추가

배경:
- 오토튠 사이클은 `source_site == site AND applied_policy_id IS NOT NULL` 필터에
  `last_refreshed_at ASC NULLS FIRST, id ASC` 정렬로 배치 SELECT 한다
  (collector_autotune.py `_site_autotune_loop`).
- 기존 인덱스는 워룸 대시보드용 `(source_site, monitor_priority, sale_status)` 라
  이 정렬/필터를 직접 커버하지 못함 → 10만+ 상품에서 정렬 비용 큼.

idempotent / hot 테이블 안전:
- samba_collected_product 는 hot 테이블 → CREATE INDEX CONCURRENTLY 로 AccessExclusiveLock
  회피 (활성 트랜잭션과 데드락 방지).
- CONCURRENTLY 는 트랜잭션 밖에서만 실행 가능 → autocommit_block 사용.
- IF NOT EXISTS 로 재실행 안전 (entrypoint stamp 재배포 시 반복 실행 대비).
- registered_accounts(market_cond) 는 기존 GIN ix_scp_registered_accounts_gin 이 커버.

Revision ID: zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz_autotune_cycle_idx
Revises: zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz_add_install_token
Create Date: 2026-05-24 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op


revision: str = "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz_autotune_cycle_idx"
down_revision: Union[str, Sequence[str], None] = (
    "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz_add_install_token"
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # CONCURRENTLY 는 트랜잭션 안에서 실패 → autocommit_block 으로 트랜잭션 밖 실행
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_scp_autotune_cycle "
            "ON samba_collected_product "
            "(source_site, last_refreshed_at ASC NULLS FIRST, id) "
            "WHERE applied_policy_id IS NOT NULL"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_scp_autotune_cycle")
