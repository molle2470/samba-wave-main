"""prod SNKRDUNK 검색필터 keyword + 최근 수집잡 상태 확인 (읽기전용)."""

import asyncio

from sqlalchemy import text

from backend.db.orm import get_read_session


async def main():
    async with get_read_session() as s:
        print("=== SNKRDUNK 검색필터 ===")
        rows = (
            await s.execute(
                text(
                    """
            SELECT id, name, keyword, category_filter, created_at
            FROM samba_search_filter
            WHERE source_site='SNKRDUNK'
            ORDER BY created_at DESC LIMIT 10
            """
                )
            )
        ).fetchall()
        for r in rows:
            print(f"  id={r[0]} name={r[1]!r}")
            print(f"     keyword={r[2]!r}")
            print(f"     category_filter={r[3]!r} created={r[4]}")

        print("\n=== 최근 SNKRDUNK 수집잡(job) ===")
        try:
            jobs = (
                await s.execute(
                    text(
                        """
                SELECT id, status, progress, total, error, created_at, updated_at
                FROM samba_jobs
                WHERE search_filter_id IN (
                    SELECT id FROM samba_search_filter WHERE source_site='SNKRDUNK'
                )
                ORDER BY created_at DESC LIMIT 8
                """
                    )
                )
            ).fetchall()
            for j in jobs:
                print(
                    f"  job={j[0]} status={j[1]} prog={j[2]}/{j[3]} "
                    f"err={j[4]!r} created={j[5]} updated={j[6]}"
                )
            if not jobs:
                print("  (SNKRDUNK 잡 없음)")
        except Exception as e:
            print(f"  job 조회 실패: {e}")


asyncio.run(main())
