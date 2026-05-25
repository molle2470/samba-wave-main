"""market_account is_default 중복 정리

배경:
- 1차 + 2차 백필 마이그레이션이 store_gmarket bare key 와 '{tenant}:store_gmarket'
  prefixed key 둘 다 처리해 같은 (tenant, market_type) 에 is_default=true 가 2개 생성됨.
- service.set_default 라디오 동작은 1개만 강제하지만 백필은 가드 누락 — 정리 필요.

처리:
- (tenant_id, market_type) 그룹 안 updated_at DESC 1순위 외 모든 row 의 is_default 를 false 로.
- 데이터는 보존(삭제 X) — additional_fields 다를 수 있어 보존이 안전.

idempotent: 이미 1개만 default 면 변경 0건.

Revision ID: zzzzzz_dedupe_market_default
Revises: zzzzz_backfill_esm_toss
Create Date: 2026-05-25 18:30:00.000000
"""

from typing import Sequence, Union

from alembic import op


revision: str = "zzzzzz_dedupe_market_default"
down_revision: Union[str, Sequence[str], None] = "zzzzz_backfill_esm_toss"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        WITH ranked AS (
            SELECT id, ROW_NUMBER() OVER (
                PARTITION BY COALESCE(tenant_id, ''), market_type
                ORDER BY updated_at DESC NULLS LAST, created_at DESC NULLS LAST, id ASC
            ) AS rn
            FROM samba_market_account
            WHERE is_default = true
        )
        UPDATE samba_market_account
        SET is_default = false, updated_at = NOW()
        WHERE id IN (SELECT id FROM ranked WHERE rn > 1)
        """
    )


def downgrade() -> None:
    pass
