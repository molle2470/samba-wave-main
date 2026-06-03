"""samba_market_account.sort_order 컬럼 실존 확인 — 프로덕션 DB (읽기 전용)."""

import asyncio
from sqlalchemy import text

from backend.db.orm import get_write_session


async def main() -> None:
    async with get_write_session() as session:
        row = (
            await session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'samba_market_account' "
                    "AND column_name = 'sort_order'"
                )
            )
        ).first()
        if row:
            print("EXISTS: sort_order 컬럼 아직 존재 — startup DROP이 실제 동작 중")
        else:
            print("ABSENT: sort_order 컬럼 이미 없음 — startup DROP은 no-op (삭제 안전)")


if __name__ == "__main__":
    asyncio.run(main())
