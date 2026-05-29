"""ABCmart 고시정보 컬럼 추가 — size_notice, heel_height, manufacture_date

이슈 #283: SSG 상품고시정보 개선 제안
ABCmart 고시정보 API에서 수집하는 치수/굽높이/제조연월을 저장하기 위한 컬럼 추가.

Revision ID: zzzzzzzzzzzzzzzzzzzzzzzzzzzzzz_add_abcmart_notice_cols
Revises: zzzzzzzzzzzzzzzzzzzzzzzzzz_scp_created_at_index
Create Date: 2026-05-29
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzz_add_abcmart_notice_cols"
down_revision: Union[str, Sequence[str], None] = (
    "zzzzzzzzzzzzzzzzzzzzzzzzzz_scp_created_at_index"
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # size_notice 이미 있는지 확인
    inspector_result = (
        op.get_context()
        .connection.execute(
            sa.text(
                """
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'samba_collected_product' AND column_name = 'size_notice'
            """
            )
        )
        .fetchone()
    )
    if not inspector_result:
        op.add_column(
            "samba_collected_product",
            sa.Column("size_notice", sa.Text(), nullable=True),
        )

    # heel_height 이미 있는지 확인
    inspector_result = (
        op.get_context()
        .connection.execute(
            sa.text(
                """
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'samba_collected_product' AND column_name = 'heel_height'
            """
            )
        )
        .fetchone()
    )
    if not inspector_result:
        op.add_column(
            "samba_collected_product",
            sa.Column("heel_height", sa.Text(), nullable=True),
        )

    # manufacture_date 이미 있는지 확인
    inspector_result = (
        op.get_context()
        .connection.execute(
            sa.text(
                """
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'samba_collected_product' AND column_name = 'manufacture_date'
            """
            )
        )
        .fetchone()
    )
    if not inspector_result:
        op.add_column(
            "samba_collected_product",
            sa.Column("manufacture_date", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("samba_collected_product", "manufacture_date")
    op.drop_column("samba_collected_product", "heel_height")
    op.drop_column("samba_collected_product", "size_notice")
