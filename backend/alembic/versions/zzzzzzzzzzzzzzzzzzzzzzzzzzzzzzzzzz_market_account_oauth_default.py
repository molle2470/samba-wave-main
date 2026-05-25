"""samba_market_account OAuth 컬럼 + is_default 추가

배경:
- `store_*` samba_settings 단일 키 폴백 폐지 → samba_market_account 단일 진실 출처 통일
- OAuth 마켓(cafe24, ebay, amazon) 토큰을 additional_fields JSON 에 두면 만료 임박 토큰
  일괄 갱신 쿼리에서 JSON 파싱 부하 큼 → 별도 컬럼으로 분리
- 다중 계정 환경에서 fallback 우선순위 식별을 위해 is_default 마킹 추가

추가 컬럼:
- oauth_access_token (Text, nullable)
- oauth_refresh_token (Text, nullable)
- oauth_expires_at (Timestamp with TZ, nullable)
- is_default (Boolean, default false, indexed)

idempotent:
- ADD COLUMN IF NOT EXISTS — 재실행 안전 (entrypoint stamp 재배포 대비)
- samba_market_account 는 hot 테이블 아님(설정성 데이터) — CONCURRENTLY 인덱스 불필요

데이터 마이그레이션은 별도 단계에서 SQL 스크립트로 실행 (자동 INSERT 는 본 파일 미포함).

Revision ID: zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz_market_account_oauth_default
Revises: zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz_autotune_cycle_idx
Create Date: 2026-05-25 12:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz_market_account_oauth_default"
down_revision: Union[str, Sequence[str], None] = (
    "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz_autotune_cycle_idx"
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(conn, table: str, column: str) -> bool:
    """information_schema 조회 — ALTER TABLE 자체 안 하면 AccessExclusiveLock 안 잡힘.
    hot 테이블 데드락 방지 (CLAUDE.md '마이그레이션 hot 테이블 데드락 방지' 참조).
    """
    return bool(
        conn.execute(
            sa.text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name=:t AND column_name=:c"
            ),
            {"t": table, "c": column},
        ).first()
    )


def _index_exists(conn, name: str) -> bool:
    return bool(
        conn.execute(
            sa.text("SELECT 1 FROM pg_indexes WHERE indexname=:n"), {"n": name}
        ).first()
    )


def upgrade() -> None:
    conn = op.get_bind()
    # hot 테이블 데드락 방지 — 컬럼 이미 있으면 ALTER 스킵 (AccessExclusiveLock 회피)
    if not _column_exists(conn, "samba_market_account", "oauth_access_token"):
        op.execute(
            "ALTER TABLE samba_market_account ADD COLUMN oauth_access_token TEXT"
        )
    if not _column_exists(conn, "samba_market_account", "oauth_refresh_token"):
        op.execute(
            "ALTER TABLE samba_market_account ADD COLUMN oauth_refresh_token TEXT"
        )
    if not _column_exists(conn, "samba_market_account", "oauth_expires_at"):
        op.execute(
            "ALTER TABLE samba_market_account "
            "ADD COLUMN oauth_expires_at TIMESTAMP WITH TIME ZONE"
        )
    if not _column_exists(conn, "samba_market_account", "is_default"):
        op.execute(
            "ALTER TABLE samba_market_account "
            "ADD COLUMN is_default BOOLEAN NOT NULL DEFAULT false"
        )
    if not _index_exists(conn, "ix_smk_default_per_market"):
        op.execute(
            "CREATE INDEX ix_smk_default_per_market "
            "ON samba_market_account (tenant_id, market_type, is_default)"
        )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_smk_default_per_market")
    op.execute("ALTER TABLE samba_market_account DROP COLUMN IF EXISTS is_default")
    op.execute(
        "ALTER TABLE samba_market_account DROP COLUMN IF EXISTS oauth_expires_at"
    )
    op.execute(
        "ALTER TABLE samba_market_account DROP COLUMN IF EXISTS oauth_refresh_token"
    )
    op.execute(
        "ALTER TABLE samba_market_account DROP COLUMN IF EXISTS oauth_access_token"
    )
