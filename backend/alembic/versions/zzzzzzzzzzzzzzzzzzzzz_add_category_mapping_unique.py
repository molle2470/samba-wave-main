"""samba_category_mapping (source_site, source_category) UNIQUE INDEX 추가

PR #126 (REXMONDE 통합 포함) 머지 후 운영 환경에서 발견된 누락 마이그레이션.

`backend/backend/domain/samba/collector/service.py`의 카테고리 매핑 자동 생성 로직이
다음 SQL을 호출:

    INSERT INTO samba_category_mapping (...) VALUES (...)
    ON CONFLICT (source_site, source_category) DO NOTHING

기존 마이그레이션에는 `(source_site, source_category)` 컬럼 조합에 대한 unique
또는 exclusion constraint가 없어서 다음 에러로 transaction이 abort되고,
이어지는 SambaCollectedProduct INSERT가 `InFailedSQLTransactionError`로 실패:

    asyncpg.exceptions.InvalidColumnReferenceError:
    there is no unique or exclusion constraint matching the ON CONFLICT specification

상품 수집 시 매번 발생 → 카테고리 매핑 미생성 + 상품 저장 실패.

UNIQUE INDEX를 추가하여 ON CONFLICT가 매칭되도록 한다. (source_site, source_category)
조합은 이미 application-level에서 unique를 가정하고 있어 데이터 충돌 위험 없음.

idempotent — IF NOT EXISTS, async alembic 환경 호환 (autocommit_block 미사용).
"""

from collections.abc import Sequence
from typing import Union

from alembic import op


revision: str = "zzzzzzzzzzzzzzzzzzzzz_category_mapping_unique"
down_revision: Union[str, Sequence[str], None] = (
    "zzzzzzzzzzzzzzzzzzzz_add_daily_registered_snapshot"
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_samba_category_mapping_site_category
        ON samba_category_mapping (source_site, source_category)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_samba_category_mapping_site_category")
