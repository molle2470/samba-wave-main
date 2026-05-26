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

운영 DDL 표준 — lock_timeout + retry DO 블록 필수:
- blue 컨테이너 폴러/sync 잡이 samba_market_account 에 RowExclusiveLock 점유 중인 상태에서
  ALTER 의 AccessExclusiveLock 단발 30s 대기 후 fail → 배포 abort (issue #241, 2026-05-25)
- 단발 대기 대신 SET LOCAL lock_timeout = '3s' + 30회 retry + pg_sleep(2) 로 양보·재시도
- 외부 마켓 API 호출이 끝나는 순간(폴러 cycle 종료)에 통과
- 표준 패턴 참고: zzzzz_tags_jsonb_gin.py (2026-05-07 deadlock 사고 후 도입)

idempotent:
- ADD COLUMN IF NOT EXISTS + CREATE INDEX IF NOT EXISTS — 재실행 안전
- samba_market_account 는 hot 테이블 아님(설정성 데이터) — CONCURRENTLY 인덱스 불필요

데이터 마이그레이션은 별도 단계에서 SQL 스크립트로 실행 (자동 INSERT 는 본 파일 미포함).

Revision ID: zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz_market_account_oauth_default
Revises: zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz_autotune_cycle_idx
Create Date: 2026-05-25 12:00:00.000000
"""

from typing import Sequence, Union

from alembic import op


revision: str = "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz_market_account_oauth_default"
down_revision: Union[str, Sequence[str], None] = (
    "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz_autotune_cycle_idx"
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _retry_block(body_sql: str) -> str:
    """ALTER/CREATE INDEX 단일 SQL 을 lock_timeout=3s + 30회 retry DO 블록으로 감쌈.

    blue 컨테이너 트랜잭션이 테이블 lock 점유 중일 때 단발 30s 대기 후 fail 대신
    3s 단위로 양보·재시도. 최대 ~150s 동안 폴러 cycle 종료 틈을 노림.
    body_sql 에는 IF NOT EXISTS 포함된 idempotent SQL 전달.
    """
    return f"""
        DO $$
        DECLARE
            attempts INTEGER := 0;
            max_attempts INTEGER := 30;
        BEGIN
            LOOP
                attempts := attempts + 1;
                BEGIN
                    SET LOCAL lock_timeout = '3s';
                    {body_sql}
                    EXIT;
                EXCEPTION
                    WHEN lock_not_available OR deadlock_detected THEN
                        IF attempts >= max_attempts THEN RAISE; END IF;
                        PERFORM pg_sleep(2);
                END;
            END LOOP;
        END
        $$;
    """


def upgrade() -> None:
    op.execute(
        _retry_block(
            "ALTER TABLE samba_market_account "
            "ADD COLUMN IF NOT EXISTS oauth_access_token TEXT;"
        )
    )
    op.execute(
        _retry_block(
            "ALTER TABLE samba_market_account "
            "ADD COLUMN IF NOT EXISTS oauth_refresh_token TEXT;"
        )
    )
    op.execute(
        _retry_block(
            "ALTER TABLE samba_market_account "
            "ADD COLUMN IF NOT EXISTS oauth_expires_at TIMESTAMP WITH TIME ZONE;"
        )
    )
    op.execute(
        _retry_block(
            "ALTER TABLE samba_market_account "
            "ADD COLUMN IF NOT EXISTS is_default BOOLEAN NOT NULL DEFAULT false;"
        )
    )
    op.execute(
        _retry_block(
            "CREATE INDEX IF NOT EXISTS ix_smk_default_per_market "
            "ON samba_market_account (tenant_id, market_type, is_default);"
        )
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
