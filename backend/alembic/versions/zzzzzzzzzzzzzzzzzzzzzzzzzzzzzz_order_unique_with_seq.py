"""samba_order unique constraint를 (tenant_id, order_number, ord_prd_seq)로 변경

11번가 한 주문에 여러 상품(ordPrdSeq) 주문 시 1개만 저장되던 사고(issue #208) 해결.
ord_prd_seq NULL은 PG가 distinct로 취급 — 타 마켓(NULL) 영향 없음.

idempotent — 이미 신규 인덱스 존재 시 ALTER 스킵.

Revision ID: zzzzzzzzzzzzzzzzzzzzzzzzzzzzzz_order_unique_with_seq
Revises: zzzzzzzzzzzzzzzzzzzzzzzzzzzzz_extension_key_device_id
Create Date: 2026-05-20
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzz_order_unique_with_seq"
down_revision: Union[str, Sequence[str], None] = (
    "zzzzzzzzzzzzzzzzzzzzzzzzzzzzz_extension_key_device_id"
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    # 이미 신규 인덱스 존재 → 스킵
    exists_new = conn.execute(
        sa.text(
            "SELECT 1 FROM pg_indexes "
            "WHERE schemaname='public' "
            "AND indexname='uq_order_tenant_number_seq'"
        )
    ).fetchone()
    if exists_new:
        return

    # 기존 unique 인덱스가 있으면 먼저 drop
    exists_old = conn.execute(
        sa.text(
            "SELECT 1 FROM pg_indexes "
            "WHERE schemaname='public' "
            "AND indexname='uq_order_tenant_number'"
        )
    ).fetchone()
    if exists_old:
        op.drop_index("uq_order_tenant_number", table_name="samba_order")

    op.create_index(
        "uq_order_tenant_number_seq",
        "samba_order",
        ["tenant_id", "order_number", "ord_prd_seq"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_order_tenant_number_seq", table_name="samba_order")
    op.create_index(
        "uq_order_tenant_number",
        "samba_order",
        ["tenant_id", "order_number"],
        unique=True,
    )
