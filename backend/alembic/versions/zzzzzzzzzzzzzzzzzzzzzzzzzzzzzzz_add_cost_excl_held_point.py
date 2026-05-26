"""samba_collected_product / samba_product에 cost_excl_held_point 컬럼 추가

배경:
- 무신사 최대혜택가는 "보유 적립금 사용" 차감액(현재 보유 적립금 × maxUsePointRate)을
  포함하여 cost가 실제보다 낮게 잡힘. 정책 토글로 제외 가능하게 하려면 두 cost를
  모두 저장해야 정책 변경 시 재수집 불필요.
- cost_excl_held_point: 보유 적립금 사용 제외한 최대혜택가. 무신사 외 소싱처는 NULL.

idempotent:
- IF NOT EXISTS 대신 information_schema 사전 확인 (CLAUDE.md hot 테이블 규칙)

Revision ID: zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz_add_cost_excl_held_point
Revises: zzzzzzzzzzzzzzzzzzzzzzzzzzzzzz_order_unique_with_seq
Create Date: 2026-05-22 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text


revision: str = "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz_add_cost_excl_held_point"
down_revision: Union[str, Sequence[str], None] = (
    "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzz_order_unique_with_seq"
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _ensure_column(table: str, column: str, ddl_type: str) -> None:
    """information_schema 확인 후 누락이면 idle 정리 + ALTER 실행."""
    conn = op.get_bind()
    row = conn.execute(
        text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = :t AND column_name = :c"
        ),
        {"t": table, "c": column},
    ).first()
    if row:
        return

    op.execute(
        """
        SELECT pg_terminate_backend(pid)
        FROM pg_stat_activity
        WHERE state = 'idle in transaction'
          AND pid <> pg_backend_pid()
        """
    )
    op.execute("SET LOCAL lock_timeout = '5min'")
    op.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}")


def upgrade() -> None:
    _ensure_column(
        "samba_collected_product", "cost_excl_held_point", "DOUBLE PRECISION"
    )
    _ensure_column("samba_product", "cost_excl_held_point", "DOUBLE PRECISION")


def downgrade() -> None:
    op.execute(
        "ALTER TABLE samba_collected_product DROP COLUMN IF EXISTS cost_excl_held_point"
    )
    op.execute("ALTER TABLE samba_product DROP COLUMN IF EXISTS cost_excl_held_point")
