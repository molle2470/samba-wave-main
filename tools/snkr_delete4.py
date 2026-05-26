"""기존 SNKRDUNK 수집상품 삭제 (사용자 요청). 타깃 삭제, 카운트 검증."""

import asyncio

from sqlalchemy import text

from backend.db.orm import get_write_session


async def main():
    async with get_write_session() as s:
        before = (
            await s.execute(
                text("SELECT count(*) FROM samba_collected_product WHERE source_site='SNKRDUNK'")
            )
        ).scalar()
        rows = (
            await s.execute(
                text(
                    "SELECT site_product_id, name FROM samba_collected_product "
                    "WHERE source_site='SNKRDUNK'"
                )
            )
        ).fetchall()
        print(f"삭제 대상 {before}건:")
        for r in rows:
            print(f"  {r[0]} {str(r[1])[:30]!r}")

        # collected 상태(마켓 미등록)만 삭제 — registered 상태는 보존(안전)
        res = await s.execute(
            text(
                "DELETE FROM samba_collected_product "
                "WHERE source_site='SNKRDUNK' AND status='collected'"
            )
        )
        await s.commit()
        after = (
            await s.execute(
                text("SELECT count(*) FROM samba_collected_product WHERE source_site='SNKRDUNK'")
            )
        ).scalar()
        print(f"삭제 완료: {res.rowcount}건 삭제, 남은 SNKRDUNK={after}건")


asyncio.run(main())
