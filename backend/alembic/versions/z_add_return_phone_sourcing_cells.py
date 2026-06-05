"""samba_return 편집칸 컬럼 추가 — customer_phone_manual, sourcing_order_no

Revision ID: z_ret_phone_srcorder_001
Revises: z_ret_editable_001
Create Date: 2026-06-05

반품교환 표의 '고객전화번호'(수기 덮어쓰기)·'소싱주문번호'(직접 입력) 칸을
수기 입력·영구 저장하기 위한 컬럼.
- customer_phone_manual: 마켓 customer_phone이 안심번호일 때 직접 덮어쓰기용.
  재동기화에 덮이지 않음.
- sourcing_order_no: 마켓 주문번호와 별개로 소싱주문번호를 직접 입력.
모두 TEXT(자유 입력). IF NOT EXISTS라 idempotent — 이미 존재하면 건너뜀.
(실제 적용은 lifecycle._apply_startup_schema_fixes 가 매 startup 보장)
"""

from typing import Sequence, Union

from alembic import op


revision: str = "z_ret_phone_srcorder_001"
down_revision: Union[str, Sequence[str], None] = "z_ret_editable_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE samba_return ADD COLUMN IF NOT EXISTS customer_phone_manual TEXT"
    )
    op.execute(
        "ALTER TABLE samba_return ADD COLUMN IF NOT EXISTS sourcing_order_no TEXT"
    )


def downgrade() -> None:
    pass
