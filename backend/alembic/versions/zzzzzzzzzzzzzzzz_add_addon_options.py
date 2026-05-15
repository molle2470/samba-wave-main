"""samba_collected_product에 addon_options + option_group_names 컬럼 추가

배경:
- 무신사 등 소싱처에서 메인 옵션(색상)과 별개 차원의 추가구성상품(예: Shoulder strap)을
  지원하는 상품이 존재. 기존 `options` 평면 리스트로는 마켓 등록 시 진짜 이중옵션으로
  분리되지 않음.
- 이중옵션 정상 등록을 위해:
  - `addon_options`: 추가구성상품 항목 (스마트스토어 productAddItems 등으로 매핑)
  - `option_group_names`: 메인 옵션 그룹명 목록 (예: ["색상","사이즈"]) — 마켓 변환 시
    optionGroupName1/2 라벨로 사용

idempotent:
- IF NOT EXISTS raw SQL 사용 (op.add_column 금지 — CLAUDE.md 규칙)

Revision ID: zzzzzzzzzzzzzzzz_add_addon_options
Revises: zzzzzzzzzzzzzzz_dedupe_collected_product_unique
Create Date: 2026-05-11 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op


revision: str = "zzzzzzzzzzzzzzzz_add_addon_options"
down_revision: Union[str, Sequence[str], None] = (
    "zzzzzzzzzzzzzzz_dedupe_collected_product_unique"
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # samba_collected_product 는 hot 테이블이라 ACCESS EXCLUSIVE 락 경합 발생.
    # idle in transaction 세션을 사전 정리하고 lock_timeout 을 충분히 늘려 ALTER.
    op.execute(
        """
        SELECT pg_terminate_backend(pid)
        FROM pg_stat_activity
        WHERE state = 'idle in transaction'
          AND pid <> pg_backend_pid()
        """
    )
    op.execute("SET LOCAL lock_timeout = '5min'")
    op.execute(
        """
        ALTER TABLE samba_collected_product
        ADD COLUMN IF NOT EXISTS addon_options JSONB
        """
    )
    op.execute(
        """
        ALTER TABLE samba_collected_product
        ADD COLUMN IF NOT EXISTS option_group_names JSONB
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE samba_collected_product DROP COLUMN IF EXISTS addon_options"
    )
    op.execute(
        "ALTER TABLE samba_collected_product DROP COLUMN IF EXISTS option_group_names"
    )
