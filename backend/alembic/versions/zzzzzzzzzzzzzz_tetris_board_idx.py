"""tetris board GROUP BY 전용 covering 인덱스

배경:
- /samba/policies (테트리스 보드) get_board() 쿼리는
    (tenant_id, source_site, BTRIM(brand), applied_policy_id) 기준 GROUP BY +
    DISTINCT ON 으로 정책-브랜드 집계 수행 (tetris/service.py:317, :350).
- 기존 ix_scp_tenant_site_brand_trim (tenant_id, source_site, btrim(brand))
    partial 인덱스가 있으나 planner 가 Parallel Seq Scan 선택 (EXPLAIN ANALYZE
    검증: Query #1 170s / Query #2 162s, 풀스캔 buffers read=43K).
- 원인: tenant_id=tn_xxx 만으로 row 33% 매칭 → seq scan 비용 < index scan +
    random IO. applied_policy_id 가 partial 조건에 빠져 covering 불가능.
- 해결: applied_policy_id 를 key 컬럼에 포함시켜 GROUP BY 전체 키 + WHERE
    조건을 인덱스만으로 만족(Index-Only Scan 가능). partial WHERE 로 매칭
    row 46K 만 인덱스 보유 → 크기 5~10MB 추정.

idempotent / hot 테이블 안전:
- samba_collected_product 는 hot 테이블 → CREATE INDEX CONCURRENTLY 로
    AccessExclusiveLock 회피 ([[feedback_tenant_backfill_pitfall]] 룰 준수).
- CONCURRENTLY 는 트랜잭션 밖에서만 실행 가능 → alembic 트랜잭션 수동 COMMIT
    후 실행 (ix_scp_autotune_cycle 마이그와 동일 패턴).
- IF NOT EXISTS 로 재실행 안전 ([[feedback_alembic_stamp_skip_pitfall]]
    entrypoint stamp 재배포 시 반복 실행 대비).

Revision ID: zzzzzzzzzzzzzz_tetris_board_idx
Revises: zzzzzzzzzzzzz_ai_image_transformed_column
Create Date: 2026-05-29 17:00:00.000000
"""

from typing import Sequence, Union

from alembic import op


revision: str = "zzzzzzzzzzzzzz_tetris_board_idx"
down_revision: Union[str, Sequence[str], None] = (
    "zzzzzzzzzzzzz_ai_image_transformed_column"
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # alembic 트랜잭션 수동 COMMIT 후 CONCURRENTLY 실행 (ix_scp_autotune_cycle
    # 패턴 그대로). env.py 가 이후 새 트랜잭션에서 alembic_version 갱신.
    conn = op.get_bind()
    conn.exec_driver_sql("COMMIT")
    conn.exec_driver_sql(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_scp_tetris_board "
        "ON samba_collected_product "
        "(tenant_id, source_site, btrim(brand), applied_policy_id) "
        "WHERE applied_policy_id IS NOT NULL "
        "  AND brand IS NOT NULL "
        "  AND btrim(brand) <> ''"
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql("COMMIT")
    conn.exec_driver_sql(
        "DROP INDEX CONCURRENTLY IF EXISTS ix_scp_tetris_board"
    )
