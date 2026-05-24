"""prod DB: ix_scp_autotune_cycle 상태 + 장기 트랜잭션/CONCURRENTLY 진단 (읽기전용)."""

import asyncio

from sqlalchemy import text

from backend.db.orm import get_read_session


async def main():
    async with get_read_session() as s:
        # 1) 인덱스 존재 + validity (INVALID = CONCURRENTLY 빌드 중단됨)
        idx = (
            await s.execute(
                text(
                    """
            SELECT c.relname AS idx, i.indisvalid, i.indisready
            FROM pg_class c
            JOIN pg_index i ON i.indexrelid = c.oid
            WHERE c.relname = 'ix_scp_autotune_cycle'
            """
                )
            )
        ).fetchall()
        print("ix_scp_autotune_cycle:", [tuple(r) for r in idx] or "없음")

        # 2) 현재 실행중인 CREATE INDEX / 장기 쿼리
        rows = (
            await s.execute(
                text(
                    """
            SELECT pid, state,
                   EXTRACT(EPOCH FROM (now()-xact_start))::int AS xact_age_s,
                   EXTRACT(EPOCH FROM (now()-query_start))::int AS query_age_s,
                   left(regexp_replace(query, '\\s+', ' ', 'g'), 90) AS q
            FROM pg_stat_activity
            WHERE datname IS NOT NULL
              AND (query ILIKE '%CREATE INDEX%' OR query ILIKE '%CONCURRENTLY%'
                   OR state = 'idle in transaction'
                   OR (state='active' AND now()-query_start > interval '30 s'))
            ORDER BY xact_age_s DESC NULLS LAST
            LIMIT 20
            """
                )
            )
        ).fetchall()
        print("\n장기/인덱스/idle-in-tx 활동:")
        for r in rows:
            print(f"  pid={r[0]} state={r[1]} xact={r[2]}s query={r[3]}s :: {r[4]}")

        # 3) alembic 현재 버전
        ver = (
            await s.execute(text("SELECT version_num FROM alembic_version"))
        ).fetchall()
        print("\nalembic_version:", [r[0] for r in ver])


asyncio.run(main())
