"""samba_order.musinsa_ord_opt_no 컬럼 추가

무신사 송장 fetch 시 trace 페이지의 `ord_opt_no` 파라미터가 필수인데
DB에 보관되지 않아 백엔드에서 deliveryInfo API 호출 불가.

확장앱이 마이페이지 API 응답을 가로채서 ord_no→ord_opt_no 매핑을 캡처하고
백엔드가 여기에 저장. 한 번 저장되면 같은 주문은 영구 사용.

idempotent — IF NOT EXISTS raw SQL.
"""

from collections.abc import Sequence
from typing import Union

from alembic import op


revision: str = "zzzzzzzzzzzzzzzzzzzzzz_musinsa_ord_opt_no"
down_revision: Union[str, Sequence[str], None] = (
    "zzzzzzzzzzzzzzzzzzzzz_category_mapping_unique"
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE samba_order
        ADD COLUMN IF NOT EXISTS musinsa_ord_opt_no TEXT
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE samba_order DROP COLUMN IF EXISTS musinsa_ord_opt_no")
