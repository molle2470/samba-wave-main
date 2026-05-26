"""samba_settings.store_{gmarket,auction,toss} → samba_market_account 추가 백필

배경:
- 1차 백필(zzzz_backfill_market_account) 에 gmarket/auction/toss 누락
- probe 결과 3마켓 store_* row 존재하나 market_account default 없음 확인
- 7c (legacy 폴백 제거) 진행 전 누락분 보충 필요

매핑:
- gmarket / auction (ESM): api_key=apiKey, seller_id=sellerId
- toss: api_key=apiKey, api_secret=apiSecret

idempotent — NOT EXISTS 가드.

Revision ID: zzzzz_backfill_esm_toss
Revises: zzzzz_autotune_cycle_index
Create Date: 2026-05-25 17:00:00.000000
"""

from typing import Sequence, Union

from alembic import op


revision: str = "zzzzz_backfill_esm_toss"
down_revision: Union[str, Sequence[str], None] = "zzzzz_autotune_cycle_index"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (market_type, korean_name, store_key, api_key_field, api_secret_field, seller_id_field)
_MARKETS = [
    ("gmarket", "G마켓", "store_gmarket", "apiKey", None, "sellerId"),
    ("auction", "옥션", "store_auction", "apiKey", None, "sellerId"),
    ("toss", "토스", "store_toss", "apiKey", "apiSecret", None),
]


def _build_insert_sql(
    market_type: str,
    korean_name: str,
    store_key: str,
    api_key_field: str | None,
    api_secret_field: str | None,
    seller_id_field: str | None,
) -> str:
    def _extract(field: str | None) -> str:
        return f"s.value::jsonb ->> '{field}'" if field else "NULL"

    api_key_expr = _extract(api_key_field)
    api_secret_expr = _extract(api_secret_field)
    seller_id_expr = _extract(seller_id_field)

    tenant_expr = (
        f"CASE WHEN s.key = '{store_key}' THEN s.tenant_id "
        f"ELSE COALESCE(s.tenant_id, split_part(s.key, chr(58), 1)) END"
    )

    id_expr = "'ma_' || substr(md5(random()::text || s.key || clock_timestamp()::text), 1, 26)"

    return f"""
    INSERT INTO samba_market_account (
        id, tenant_id, market_type, market_name, account_label,
        api_key, api_secret, seller_id, additional_fields,
        is_active, is_default, created_at, updated_at
    )
    SELECT
        {id_expr},
        {tenant_expr} AS tenant_id_extracted,
        '{market_type}',
        '{korean_name}',
        'default',
        {api_key_expr},
        {api_secret_expr},
        {seller_id_expr},
        s.value,
        true,
        true,
        NOW(),
        NOW()
    FROM samba_settings s
    WHERE (s.key = '{store_key}' OR s.key LIKE '%' || chr(58) || '{store_key}')
      AND s.value IS NOT NULL
      AND s.value::text NOT IN ('null', '{{}}', '""')
      AND NOT EXISTS (
        SELECT 1 FROM samba_market_account ma
        WHERE COALESCE(ma.tenant_id, '') = COALESCE({tenant_expr}, '')
          AND ma.market_type = '{market_type}'
          AND ma.is_default = true
      )
    """


def upgrade() -> None:
    for market in _MARKETS:
        op.execute(_build_insert_sql(*market))


def downgrade() -> None:
    pass
