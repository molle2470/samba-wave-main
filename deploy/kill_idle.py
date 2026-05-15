"""alembic_version lock을 잡고 있는 idle in transaction 세션 강제 종료.

VM 컨테이너 내부에서 실행:
    docker cp deploy/kill_idle.py samba-samba-api-1:/tmp/kill_idle.py
    docker exec samba-samba-api-1 /app/backend/.venv/bin/python /tmp/kill_idle.py
remaining=0 될 때까지 반복.
"""

from __future__ import annotations

import asyncio

import asyncpg

from backend.core.config import settings


async def main() -> None:
    conn = await asyncpg.connect(
        user=settings.write_db_user,
        password=settings.write_db_password,
        host=settings.write_db_host,
        port=settings.write_db_port,
        database=settings.write_db_name,
        ssl=False,
    )
    try:
        before = await conn.fetch(
            """
            SELECT pid, state, query_start, left(query, 80) AS q
            FROM pg_stat_activity
            WHERE state = 'idle in transaction'
            ORDER BY query_start
            """
        )
        print(f"[before] idle in transaction: {len(before)}건")
        for r in before:
            print(f"  pid={r['pid']} start={r['query_start']} q={r['q']}")

        killed = await conn.fetch(
            """
            SELECT pg_terminate_backend(pid) AS ok, pid
            FROM pg_stat_activity
            WHERE state = 'idle in transaction'
            """
        )
        print(f"[kill] {len(killed)}건 terminate 시도")

        await asyncio.sleep(1)
        after = await conn.fetch(
            "SELECT count(*) AS n FROM pg_stat_activity WHERE state='idle in transaction'"
        )
        remaining = after[0]["n"]
        print(f"[after] remaining idle in transaction: {remaining}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
