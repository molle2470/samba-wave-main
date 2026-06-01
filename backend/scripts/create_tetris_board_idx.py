"""테트리스 보드 covering 인덱스 프로덕션 수동 생성 (PR #307 사전작업).

CONCURRENTLY 로 hot 테이블 AccessExclusiveLock 회피.
수동 선생성 → 배포 시 마이그레이션 IF NOT EXISTS 가 skip → 오토튠 트랜잭션 드레인
대기로 인한 green 360s 타임아웃 배포차단 회피.
"""

import asyncio
import time

import asyncpg

from backend.core.config import settings

INDEX_SQL = (
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_scp_tetris_board "
    "ON samba_collected_product "
    "(tenant_id, source_site, btrim(brand), applied_policy_id) "
    "WHERE applied_policy_id IS NOT NULL "
    "  AND brand IS NOT NULL "
    "  AND btrim(brand) <> ''"
)


async def main() -> None:
    conn = await asyncpg.connect(
        host=settings.write_db_host,
        port=settings.write_db_port,
        user=settings.write_db_user,
        password=settings.write_db_password,
        database=settings.write_db_name,
        ssl=settings.use_db_ssl or None,
    )
    try:
        # 생성 전 존재 여부 확인
        before = await conn.fetchval(
            "SELECT indexname FROM pg_indexes WHERE indexname = 'ix_scp_tetris_board'"
        )
        print(f"[before] 인덱스 존재: {before}")

        t0 = time.monotonic()
        # CONCURRENTLY 는 트랜잭션 밖에서 실행 (asyncpg execute 는 autocommit)
        await conn.execute(INDEX_SQL)
        dur = time.monotonic() - t0
        print(f"[create] 완료 — {dur:.1f}s")

        # 생성 후 + 유효성(valid) 확인
        row = await conn.fetchrow(
            "SELECT c.relname, i.indisvalid "
            "FROM pg_class c JOIN pg_index i ON i.indexrelid = c.oid "
            "WHERE c.relname = 'ix_scp_tetris_board'"
        )
        print(f"[after] {row}")
        if row and not row["indisvalid"]:
            print("[WARN] 인덱스 indisvalid=False — CONCURRENTLY 빌드 실패 가능. 재확인 필요")

        # 인덱스 크기
        size = await conn.fetchval(
            "SELECT pg_size_pretty(pg_relation_size('ix_scp_tetris_board'))"
        )
        print(f"[size] {size}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
