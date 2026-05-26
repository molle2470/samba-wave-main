"""samba_cs_inquiry 누락 컬럼 전체 추가

Revision ID: zzzzzzzz_cs_inquiry_missing_columns
Revises: zzzzzzz_lotteon_dedupe
Create Date: 2026-05-26
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "zzzzzzzz_cs_inquiry_missing_columns"
down_revision: Union[str, Sequence[str], None] = "zzzzzzz_lotteon_dedupe"
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


def _idx_exists(conn, idx: str) -> bool:
    result = conn.execute(
        sa.text("SELECT 1 FROM pg_indexes WHERE indexname = :i"),
        {"i": idx},
    ).fetchone()
    return result is not None


def upgrade() -> None:
    conn = op.get_bind()
    tbl = "samba_cs_inquiry"

    # (컬럼명, SQLAlchemy 타입, nullable, server_default)
    columns = [
        ("market_order_id", sa.Text(), True, None),
        ("market_inquiry_no", sa.Text(), True, None),
        ("market_answer_no", sa.Text(), True, None),
        ("account_id", sa.Text(), True, None),
        ("account_name", sa.Text(), True, None),
        ("external_id", sa.Text(), True, None),
        ("external_sent", sa.Boolean(), False, "false"),
        ("inquiry_type", sa.Text(), False, "'general'"),
        ("questioner", sa.Text(), True, None),
        ("collected_product_id", sa.Text(), True, None),
        ("market_product_no", sa.Text(), True, None),
        ("product_name", sa.Text(), True, None),
        ("product_image", sa.Text(), True, None),
        ("product_link", sa.Text(), True, None),
        ("market_link", sa.Text(), True, None),
        ("original_link", sa.Text(), True, None),
        ("reply", sa.Text(), True, None),
        ("reply_status", sa.Text(), False, "'pending'"),
        ("is_hidden", sa.Boolean(), False, "false"),
        ("replied_at", sa.DateTime(timezone=True), True, None),
        ("inquiry_date", sa.DateTime(timezone=True), True, None),
        ("collected_at", sa.DateTime(timezone=True), True, "now()"),
        ("board_no", sa.Text(), True, None),
    ]

    for col_name, col_type, nullable, server_default in columns:
        if not _col_exists(conn, tbl, col_name):
            col = sa.Column(col_name, col_type, nullable=nullable)
            if server_default:
                col = sa.Column(
                    col_name,
                    col_type,
                    nullable=nullable,
                    server_default=sa.text(server_default),
                )
            op.add_column(tbl, col)

    # 인덱스
    indexes = [
        ("ix_samba_cs_inquiry_market_inquiry_no", "market_inquiry_no"),
        ("ix_samba_cs_inquiry_market", "market"),
        ("ix_samba_cs_inquiry_reply_status", "reply_status"),
        ("ix_samba_cs_inquiry_account_id", "account_id"),
        ("ix_samba_cs_inquiry_market_order_id", "market_order_id"),
        ("ix_samba_cs_inquiry_market_product_no", "market_product_no"),
        ("ix_samba_cs_inquiry_external_id", "external_id"),
        ("ix_samba_cs_inquiry_inquiry_type", "inquiry_type"),
        ("ix_samba_cs_inquiry_collected_product_id", "collected_product_id"),
    ]
    for idx_name, col_name in indexes:
        if not _idx_exists(conn, idx_name) and _col_exists(conn, tbl, col_name):
            op.create_index(idx_name, tbl, [col_name], unique=False)

    # "LOTTEHOME" → "롯데홈쇼핑" 데이터 정규화 (market 값 통일)
    conn.execute(
        sa.text(
            "UPDATE samba_cs_inquiry SET market = '롯데홈쇼핑' WHERE market = 'LOTTEHOME'"
        )
    )


def downgrade() -> None:
    pass
