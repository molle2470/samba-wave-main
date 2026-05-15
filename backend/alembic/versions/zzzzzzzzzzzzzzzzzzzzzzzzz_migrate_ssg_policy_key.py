"""samba_policy.market_policies JSON에서 '신세계몰' 키를 '신세계몰(전시)'로 이름 변경

SSG 플러그인 policy_key가 '신세계몰' → '신세계몰(전시)'로 변경됨.
운영 DB에 기존 키로 저장된 정책 오버라이드(마진율, 배송비 등)가 적용 안 되는 문제 수정.

idempotent — ? 연산자로 키 존재 여부 확인 후 업데이트.

Revision ID: zzzzzzzzzzzzzzzzzzzzzzzzz_ssg_policy_key
Revises: zzzzzzzzzzzzzzzzzzzzzzzz_merge_heads_postal_sales
Create Date: 2026-05-15
"""

from typing import Sequence, Union

from alembic import op

revision: str = "zzzzzzzzzzzzzzzzzzzzzzzzz_ssg_policy_key"
down_revision: Union[str, Sequence[str], None] = (
    "zzzzzzzzzzzzzzzzzzzzzzzz_merge_heads_postal_sales"
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        UPDATE samba_policy
        SET market_policies = (
            (market_policies::jsonb - '신세계몰')
            || jsonb_build_object('신세계몰(전시)', market_policies::jsonb -> '신세계몰')
        )
        WHERE market_policies IS NOT NULL
          AND market_policies::jsonb ? '신세계몰'
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE samba_policy
        SET market_policies = (
            (market_policies::jsonb - '신세계몰(전시)')
            || jsonb_build_object('신세계몰', market_policies::jsonb -> '신세계몰(전시)')
        )
        WHERE market_policies IS NOT NULL
          AND market_policies::jsonb ? '신세계몰(전시)'
    """)
