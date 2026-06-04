"""samba_return 편집칸 컬럼 추가 — customer_amount, company_amount, return_link_manual

Revision ID: z_ret_editable_001
Revises: y2z3a4b5c6d7
Create Date: 2026-06-04

반품교환 표의 '고객'/'회사'/'반품링크' 칸을 수기 입력·영구 저장하기 위한 컬럼.
모두 TEXT(자유 입력). IF NOT EXISTS라 idempotent — 이미 존재하면 건너뜀.
(실제 적용은 lifecycle._apply_startup_schema_fixes 가 매 startup 보장)
"""

from typing import Sequence, Union

from alembic import op


revision: str = "z_ret_editable_001"
down_revision: Union[str, Sequence[str], None] = "y2z3a4b5c6d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE samba_return ADD COLUMN IF NOT EXISTS customer_amount TEXT"
    )
    op.execute(
        "ALTER TABLE samba_return ADD COLUMN IF NOT EXISTS company_amount TEXT"
    )
    op.execute(
        "ALTER TABLE samba_return ADD COLUMN IF NOT EXISTS return_link_manual TEXT"
    )


def downgrade() -> None:
    pass
