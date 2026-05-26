"""samba_collected_product coupang_search_tags 컬럼 추가 — 쿠팡 전용 롱테일 검색어 10개

Revision ID: zzzzzzzzzz_coupang_search_tags
Revises: zzzzzzzzz_vendor_item_id
Create Date: 2026-05-27
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "zzzzzzzzzz_coupang_search_tags"
down_revision: Union[str, Sequence[str], None] = "zzzzzzzzz_vendor_item_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _col_exists(conn, table: str, col: str) -> bool:
    result = conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = :t AND column_name = :c"
        ),
        {"t": table, "c": col},
    ).fetchone()
    return result is not None


def upgrade() -> None:
    # hot 테이블(samba_collected_product) AccessExclusiveLock 회피 — 존재 시 스킵.
    conn = op.get_bind()
    if not _col_exists(conn, "samba_collected_product", "coupang_search_tags"):
        op.execute(
            "ALTER TABLE samba_collected_product "
            "ADD COLUMN IF NOT EXISTS coupang_search_tags JSONB"
        )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE samba_collected_product DROP COLUMN IF EXISTS coupang_search_tags"
    )
