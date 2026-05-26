"""prod: ix_scp_autotune_cycle 수동 사전 생성 (CONCURRENTLY, autocommit).

green startup alembic CONCURRENTLY 가 360s 안에 못 끝나 배포 실패 → 인덱스를
미리 만들어 두면 마이그레이션 IF NOT EXISTS 가 skip → alembic 즉시 완료.

- asyncpg 직접 연결(autocommit) — CONCURRENTLY 는 트랜잭션 밖에서만 가능
- statement_timeout=0 — 빌드 중간 kill 방지
- SQL 은 마이그레이션 파일과 byte-for-byte 동일
- 비파괴(추가 전용, 무락). DROP 안 함.
"""

import asyncio

import asyncpg

from backend.core.config import settings

# 마이그레이션(zzzz..._autotune_cycle_idx.py)과 동일한 SQL
CREATE_SQL = (
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_scp_autotune_cycle "
    "ON samba_collected_product "
    "(source_site, last_refreshed_at ASC NULLS FIRST, id) "
    "WHERE applied_policy_id IS NOT NULL"
)


async def main():
    conn = await asyncpg.connect(
        host=settings.write_db_host,
        port=settings.write_db_port,
        user=settings.write_db_user,
        password=settings.write_db_password,
        database=settings.write_db_name,
        ssl=False,
    )
    try:
        st = await conn.fetchval("SHOW statement_timeout")
        print(f"statement_timeout(before)={st}")

        longest = await conn.fetchval(
            """
            SELECT max(EXTRACT(EPOCH FROM (now()-xact_start)))
            FROM pg_stat_activity
            WHERE state IN ('active','idle in transaction')
              AND query ILIKE '%samba_collected_product%'
            """
        )
        print(f"samba_collected_product 최장 트랜잭션={longest}s")

        # 이 세션 statement_timeout 해제 (CONCURRENTLY 빌드 보호)
        await conn.execute("SET statement_timeout = 0")
        print("statement_timeout=0 적용. CREATE INDEX CONCURRENTLY 시작...")

        await conn.execute(CREATE_SQL)
        print("CREATE INDEX 반환됨.")

        row = await conn.fetchrow(
            """
            SELECT i.indisvalid, i.indisready
            FROM pg_class c JOIN pg_index i ON i.indexrelid=c.oid
            WHERE c.relname='ix_scp_autotune_cycle'
            """
        )
        if row is None:
            print("결과: 인덱스 없음 (생성 실패?)")
        else:
            print(
                f"결과: indisvalid={row['indisvalid']} indisready={row['indisready']}"
            )
            if row["indisvalid"]:
                print("OK — 유효한 인덱스 생성 완료. 재배포 가능.")
            else:
                print("주의 — INVALID 인덱스. DROP 없이 임의 조치 금지, 보고 필요.")
    finally:
        await conn.close()


asyncio.run(main())
