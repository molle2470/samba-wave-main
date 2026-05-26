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
    # CONCURRENTLY 는 트랜잭션 안에서 실행 불가.
    # 이 레포 env.py는 async(asyncpg) + connection.run_sync 구조라 alembic
    # autocommit_block 이 _transaction 을 추적 못 해 AssertionError 발생(검증 완료).
    # → alembic 트랜잭션을 수동 COMMIT 으로 종료한 뒤 CONCURRENTLY 실행.
    #   alembic_version UPDATE 는 이후 새 암묵 트랜잭션에서 처리되고 env.py 가 최종 commit.
    conn = op.get_bind()
    conn.exec_driver_sql("COMMIT")
    conn.exec_driver_sql(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_scp_autotune_cycle "
        "ON samba_collected_product "
        "(source_site, last_refreshed_at ASC NULLS FIRST, id) "
        "WHERE applied_policy_id IS NOT NULL"
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql("COMMIT")
    conn.exec_driver_sql("DROP INDEX CONCURRENTLY IF EXISTS ix_scp_autotune_cycle")
