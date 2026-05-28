"""ai_image_transformed boolean 컬럼 추가 + backfill

Revision ID: zzzzzzzzzzzzz_ai_image_transformed_column
Revises: zzzzzzzzzzzz_collected_image_mirror_map
Create Date: 2026-05-28
"""

from typing import Sequence, Union

from alembic import op

revision: str = "zzzzzzzzzzzzz_ai_image_transformed_column"
down_revision: Union[str, Sequence[str], None] = (
    "zzzzzzzzzzzz_collected_image_mirror_map"
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # lock_timeout 30초 — hot 테이블 ALTER 데드락 방지
    conn.execute(__import__("sqlalchemy").text("SET lock_timeout = '30s'"))

    # IF NOT EXISTS 패턴 — idempotent
    conn.execute(
        __import__("sqlalchemy").text(
            """
            ALTER TABLE samba_collected_product
            ADD COLUMN IF NOT EXISTS ai_image_transformed BOOLEAN NOT NULL DEFAULT FALSE
            """
        )
    )

    # backfill: tags 배열에 __ai_image__ 포함된 기존 row → TRUE
    conn.execute(
        __import__("sqlalchemy").text(
            """
            UPDATE samba_collected_product
            SET ai_image_transformed = TRUE
            WHERE ai_image_transformed = FALSE
              AND tags @> '["__ai_image__"]'::jsonb
            """
        )
    )

    conn.execute(__import__("sqlalchemy").text("SET lock_timeout = '0'"))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        __import__("sqlalchemy").text(
            "ALTER TABLE samba_collected_product DROP COLUMN IF EXISTS ai_image_transformed"
        )
    )
