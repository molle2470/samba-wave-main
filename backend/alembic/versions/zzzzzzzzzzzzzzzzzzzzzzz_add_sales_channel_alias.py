"""samba_order.sales_channel_alias 컬럼 추가

PlayAuto 임포트 주문은 1 채널(플레이오토) × 다 site_id(GS이숍/롯데홈쇼핑 등) 구조라
실제 판매처 별칭(예: "GS이숍(캐논)", "롯데홈쇼핑(037800LT)")이 필요하다.

기존에는 이 별칭을 source_site 컬럼에 넣어 의미가 오염됨 — 송장 추출이
"GS이숍(캐논)"을 소싱처 코드로 오인해 실패. 별도 컬럼으로 분리해 source_site는
진짜 소싱처 코드(MUSINSA/LOTTEON/SSG 등)만 보관하도록 정상화.

idempotent — IF NOT EXISTS raw SQL.
"""

from collections.abc import Sequence
from typing import Union

from alembic import op


revision: str = "zzzzzzzzzzzzzzzzzzzzzzz_sales_channel_alias"
down_revision: Union[str, Sequence[str], None] = (
    "zzzzzzzzzzzzzzzzzzzzzz_musinsa_ord_opt_no"
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE samba_order
        ADD COLUMN IF NOT EXISTS sales_channel_alias TEXT
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE samba_order DROP COLUMN IF EXISTS sales_channel_alias")
