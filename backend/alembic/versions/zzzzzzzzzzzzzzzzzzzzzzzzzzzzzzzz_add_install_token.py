"""samba_extension_key에 is_install_token 컬럼 추가

배경:
- 데몬 다운로드 시 발급하는 1시간 만료 install-token 을 long-lived 키와 구분.
- 데몬 첫 실행 시 /extension-keys/exchange 로 long-lived 키 교환 후 install-token revoke.
- is_install_token=True 키는 api_gateway 일반 인증에서 거부, exchange 전용.

idempotent:
- ADD COLUMN IF NOT EXISTS — samba_extension_key 는 hot 테이블 아님(소량).

Revision ID: zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz_add_install_token
Revises: zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz_add_cost_excl_held_point
Create Date: 2026-05-23 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op


revision: str = "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz_add_install_token"
down_revision: Union[str, Sequence[str], None] = (
    "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz_add_cost_excl_held_point"
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE samba_extension_key "
        "ADD COLUMN IF NOT EXISTS is_install_token BOOLEAN NOT NULL DEFAULT false"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE samba_extension_key DROP COLUMN IF EXISTS is_install_token")
