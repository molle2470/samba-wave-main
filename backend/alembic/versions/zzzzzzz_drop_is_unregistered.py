"""is_unregistered 컬럼 제거 — registered_accounts 배열을 단일 등록 기준으로 통일

is_unregistered(boolean)와 registered_accounts(JSONB)가 동일한 개념을 중복 표현하면서
일부 코드 경로(PlayAuto 싱크, 롯데홈 플러그인 등)가 한쪽만 업데이트해 불일치 발생.
registered_accounts를 단일 기준으로 사용하고 표현식 인덱스로 성능 보완.

Revision ID: zzzzzzz_drop_is_unregistered
Revises: zzzzzz_lotteon_order_dedup_fix
Create Date: 2026-05-08 00:00:00.000000
"""

from alembic import op

revision = "zzzzzzz_drop_is_unregistered"
down_revision = "dd3eaff7233e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 2026-05-14 사고 방지: ALTER TABLE은 AccessExclusiveLock 필요 → 운영 중 오토튠
    # RowExclusiveLock과 데드락 가능. 마이그레이션 락 대기 중 운영 쿼리도 함께 cancel되어
    # 사용자 화면에 "greenlet_spawn..." 에러 도배. 트랜잭션 로컬 락 타임아웃을 3초로 짧게
    # 잡아, 락 못 잡으면 빠르게 fail-fast하고 다음 retry에 위임 (env.py 전역 30s보다 우선).
    op.execute("SET LOCAL lock_timeout = '3s'")

    # registered_accounts가 비어있지 않은 상품을 빠르게 필터링하는 표현식 인덱스
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_scp_has_registered_accounts
        ON samba_collected_product (
            (registered_accounts IS NOT NULL AND registered_accounts != '[]'::jsonb)
        )
    """)

    # 컬럼 존재 여부를 catalog 조회로 먼저 확인 (정보 스키마는 테이블 락 불필요)
    # → 존재하지 않으면 ALTER 자체를 스킵하여 AccessExclusiveLock 시도를 회피
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'samba_collected_product'
                  AND column_name = 'is_unregistered'
            ) THEN
                EXECUTE 'DROP INDEX IF EXISTS ix_samba_collected_product_is_unregistered';
                EXECUTE 'ALTER TABLE samba_collected_product DROP COLUMN is_unregistered';
            END IF;
        END $$;
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE samba_collected_product
        ADD COLUMN IF NOT EXISTS is_unregistered BOOLEAN NOT NULL DEFAULT TRUE
    """)
    op.execute("""
        UPDATE samba_collected_product
        SET is_unregistered = NOT (
            registered_accounts IS NOT NULL AND registered_accounts != '[]'::jsonb
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_samba_collected_product_is_unregistered
        ON samba_collected_product (is_unregistered)
    """)
    op.execute("""
        DROP INDEX IF EXISTS ix_scp_has_registered_accounts
    """)
