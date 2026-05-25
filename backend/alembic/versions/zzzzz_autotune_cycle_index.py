"""autotune cycle SELECT 인덱스 추가

배경:
- _site_autotune_loop 첫 사이클 SELECT 쿼리:
  `WHERE source_site=? AND ... ORDER BY last_refreshed_at ASC NULLS FIRST, id ASC LIMIT 200`
- 78k 상품 환경에서 24~94초 풀스캔 → 사용자 "시작 후 5분 지연" 사고
- 적합 복합 인덱스 부재 (기존 인덱스: source_site GIN trgm, monitor_priority 등 — 정렬 미커버)

추가 인덱스:
- ix_scp_autotune_cycle (source_site, last_refreshed_at NULLS FIRST, id)
  WHERE applied_policy_id IS NOT NULL
- partial — 정책 적용 상품만 (autotune 대상). 인덱스 크기 절감.

hot 테이블 (samba_collected_product) → CONCURRENTLY 필수 (테이블 잠금 회피).
배포 차단 우려는 backend/alembic/env.py 의 autocommit_block + COMMIT 처리에 의존.

Revision ID: zzzzz_autotune_cycle_index
Revises: zzzz_backfill_market_account
Create Date: 2026-05-25 17:30:00.000000
"""

from typing import Sequence, Union

from alembic import op


revision: str = "zzzzz_autotune_cycle_index"
down_revision: Union[str, Sequence[str], None] = "zzzz_backfill_market_account"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    # CONCURRENTLY 는 트랜잭션 안에서 못 실행 → 명시적 COMMIT 후 실행
    conn.exec_driver_sql("COMMIT")
    conn.exec_driver_sql(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_scp_autotune_cycle "
        "ON samba_collected_product (source_site, last_refreshed_at NULLS FIRST, id) "
        "WHERE applied_policy_id IS NOT NULL"
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql("COMMIT")
    conn.exec_driver_sql("DROP INDEX CONCURRENTLY IF EXISTS ix_scp_autotune_cycle")
