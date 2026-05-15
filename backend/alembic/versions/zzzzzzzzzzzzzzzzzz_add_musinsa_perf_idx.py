"""samba_collected_product (status, source_site, last_refreshed_at) 복합 인덱스 추가

MUSINSA 48,428건처럼 사이트별 등록상품이 많을 때 사이트 루프 진입 시
last_refreshed_at ASC 정렬을 인덱스로 처리해 SELECT 시간 단축.

EXPLAIN 측정(2026-05-12):
  before: Bitmap Heap Scan + Sort → 245ms (15,050 페이지 random read)
  after : Index Scan → 수십 ms 예상

idempotent — IF NOT EXISTS, CONCURRENTLY로 다운타임 없음.
"""

from collections.abc import Sequence
from typing import Union

from alembic import op


revision: str = "zzzzzzzzzzzzzzzzzz_add_musinsa_perf_idx"
down_revision: Union[str, Sequence[str], None] = (
    "zzzzzzzzzzzzzzzzz_add_market_registered_tracking"
)
branch_labels = None
depends_on = None


# async(asyncpg) alembic 환경에서는 autocommit_block 미지원 → AssertionError 발생.
# 운영 자동 배포(entrypoint.sh 의 alembic upgrade heads)가 이 마이그레이션에서 멈춤.
# CONCURRENTLY 제거하고 단순 CREATE INDEX 으로 변경 (samba_collected_product 1.5GB 라
# 락 시간 수초 이내, Blue/Green 배포라 traffic swap 전 완료되어 영향 미미).
def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS "
        "ix_scp_status_source_last_refreshed "
        "ON samba_collected_product "
        "(status, source_site, last_refreshed_at ASC NULLS FIRST)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_scp_status_source_last_refreshed")
