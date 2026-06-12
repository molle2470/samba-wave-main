"""samba_forbidden_word.market 컬럼 추가 — 마켓별 금지어/삭제어 분리

배경:
- 기존 금지어/삭제어/옵션삭제어는 마켓 구분 없이 전역 적용이었음.
- 마켓별 별도 설정 요구 → market 컬럼(NULL=공통) 추가.
- 적용 규칙: '공통(market IS NULL) + 해당 마켓' 합산(additive).

idempotent:
- ADD COLUMN IF NOT EXISTS / CREATE INDEX IF NOT EXISTS 로 재실행 안전
  (entrypoint stamp 재배포 반복 대비).
- samba_forbidden_word 는 hot 테이블 아님(단어 수백~수천) → 일반 ALTER 안전.

Revision ID: zzzzzzzz_add_forbidden_word_market
Revises: zzzzzzz_order_shipment_id_idx
Create Date: 2026-06-11 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op


revision: str = "zzzzzzzz_add_forbidden_word_market"
down_revision: Union[str, Sequence[str], None] = "zzzzzzz_order_shipment_id_idx"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql(
        "ALTER TABLE samba_forbidden_word ADD COLUMN IF NOT EXISTS market VARCHAR"
    )
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_samba_forbidden_word_market "
        "ON samba_forbidden_word (market)"
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql("DROP INDEX IF EXISTS ix_samba_forbidden_word_market")
    conn.exec_driver_sql(
        "ALTER TABLE samba_forbidden_word DROP COLUMN IF EXISTS market"
    )
