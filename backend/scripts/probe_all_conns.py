"""전체 DB 연결 점검 — 모든 데이터베이스 포함. 읽기 전용."""

import asyncio

import asyncpg

from backend.core.config import settings


async def main() -> None:
    conn = await asyncpg.connect(
        host=settings.write_db_host,
        port=settings.write_db_port,
        user=settings.write_db_user,
        password=settings.write_db_password,
        database=settings.write_db_name,
        ssl=False,
    )
    try:
        total = await conn.fetchval("SELECT count(*) FROM pg_stat_activity")
        mx = await conn.fetchval("SHOW max_connections")
        rsv = await conn.fetchval("SHOW superuser_reserved_connections")
        print(f"[TOTAL] all_db={total} max_connections={mx} reserved={rsv}")

        rows = await conn.fetch(
            """
            SELECT COALESCE(datname, '(background)') AS db,
                   COALESCE(usename, '(system)') AS usr,
                   COALESCE(state, '(no-state)') AS st,
                   count(*) AS cnt,
                   COALESCE(max(EXTRACT(EPOCH FROM (now() - state_change)))::int, 0) AS max_age
            FROM pg_stat_activity
            GROUP BY 1, 2, 3
            ORDER BY cnt DESC
            LIMIT 30
            """
        )
        for r in rows:
            print(
                f"  db={r['db']:16} usr={r['usr']:16} state={r['st']:22} "
                f"cnt={r['cnt']:3d} max_age={r['max_age']}s"
            )
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
