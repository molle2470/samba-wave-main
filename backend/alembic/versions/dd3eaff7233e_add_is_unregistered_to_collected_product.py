"""add_is_unregistered_to_collected_product

Revision ID: dd3eaff7233e
Revises: zzzzz_tags_jsonb_gin
Create Date: 2026-05-07 00:54:41.805217

"""

from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = "dd3eaff7233e"
down_revision: Union[str, Sequence[str], None] = "zzzzz_tags_jsonb_gin"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # NO-OP: 이 마이그레이션 직후 zzzzzzz_drop_is_unregistered 가 이 컬럼을
    # 즉시 DROP하므로 net-zero 변경. 프로덕션 samba_collected_product는
    # 10만+ 레코드의 hot 테이블이라 ALTER TABLE이 ACCESS EXCLUSIVE 락을
    # 얻지 못해 LockNotAvailableError 발생(워커들이 끊임없이 SELECT/UPDATE
    # 중). 어차피 다음 revision이 net-zero로 되돌리므로 컬럼/백필/인덱스
    # 작업 전부 skip해 배포 자체를 통과시키는 것이 안전하다.
    pass


def downgrade() -> None:
    # downgrade도 net-zero이므로 no-op.
    pass
