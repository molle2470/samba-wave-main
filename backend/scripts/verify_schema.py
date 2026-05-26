"""배포 시 스키마 정합성 검증 스크립트

프로덕션 DB의 실제 컬럼과 SQLModel 메타데이터를 비교.
누락 컬럼이 있으면 exit 1 → 서버 시작 차단 → Cloud Run이 이전 리비전 유지.

사용법 (entrypoint.sh에서 호출):
  uv run python scripts/verify_schema.py
"""

import asyncio
import os
import sys

# 프로젝트 루트를 path에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def _env(key: str) -> str:
    return (
        os.environ.get(key)
        or os.environ.get(key.lower())
        or os.environ.get(key.upper())
        or ""
    )


def get_model_columns() -> dict[str, set[str]]:
    """SQLModel 메타데이터에서 samba_ 테이블의 컬럼 목록 추출."""
    from sqlmodel import SQLModel

    # 모델 import (env.py와 동일)
    import backend.domain.samba.product.model  # noqa: F401
    import backend.domain.samba.order.model  # noqa: F401
    import backend.domain.samba.channel.model  # noqa: F401
    import backend.domain.samba.policy.model  # noqa: F401
    import backend.domain.samba.collector.model  # noqa: F401
    import backend.domain.samba.category.model  # noqa: F401
    import backend.domain.samba.account.model  # noqa: F401
    import backend.domain.samba.shipment.model  # noqa: F401
    import backend.domain.samba.forbidden.model  # noqa: F401
    import backend.domain.samba.contact.model  # noqa: F401
    import backend.domain.samba.returns.model  # noqa: F401
    import backend.domain.samba.warroom.model  # noqa: F401
    import backend.domain.samba.user.model  # noqa: F401
    import backend.domain.samba.job.model  # noqa: F401
    import backend.domain.samba.store_care.model  # noqa: F401
    import backend.domain.samba.wholesale.model  # noqa: F401
    import backend.domain.samba.license.model  # noqa: F401

    result: dict[str, set[str]] = {}
    for table in SQLModel.metadata.tables.values():
        if table.name.startswith("samba_"):
            result[table.name] = {col.name for col in table.columns}
    return result


async def get_db_columns(
    host: str, user: str, password: str, database: str, port: int
) -> dict[str, set[str]]:
    """프로덕션 DB의 information_schema에서 실제 컬럼 목록 조회."""
    import asyncpg

    kw: dict = dict(user=user, password=password, database=database)
    if host.startswith("/"):
        kw["host"] = host
    else:
        kw["host"] = host
        kw["port"] = port

    conn = await asyncpg.connect(**kw)
    try:
        rows = await conn.fetch(
            """
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name LIKE 'samba_%'
            ORDER BY table_name, ordinal_position
            """
        )
        result: dict[str, set[str]] = {}
        for row in rows:
            table = row["table_name"]
            col = row["column_name"]
            result.setdefault(table, set()).add(col)
        return result
    finally:
        await conn.close()


async def verify():
    host = _env("WRITE_DB_HOST")
    if not host:
        print("WRITE_DB_HOST not set, skip schema verification")
        return True

    user = _env("WRITE_DB_USER") or "postgres"
    password = _env("WRITE_DB_PASSWORD")
    database = _env("WRITE_DB_NAME") or "railway"
    port = int(_env("WRITE_DB_PORT") or 5432)

    model_tables = get_model_columns()
    db_tables = await get_db_columns(host, user, password, database, port)

    missing_columns: list[str] = []
    for table_name, model_cols in model_tables.items():
        db_cols = db_tables.get(table_name, set())
        if not db_cols:
            # 테이블 자체가 없으면 alembic이 만들어야 함
            missing_columns.append(f"  테이블 누락: {table_name}")
            continue
        diff = model_cols - db_cols
        for col in sorted(diff):
            missing_columns.append(f"  {table_name}.{col}")

    if missing_columns:
        print("=" * 60)
        print("FATAL: 모델과 DB 스키마 불일치 — 서버 시작 차단")
        print("=" * 60)
        print("누락된 컬럼:")
        for m in missing_columns:
            print(m)
        print()
        print("마이그레이션 파일을 생성하고 다시 배포하세요:")
        print('  alembic revision --autogenerate -m "누락 컬럼 추가"')
        print("=" * 60)
        return False

    print("✓ DB 스키마와 모델이 일치합니다.")
    return True


def main():
    ok = asyncio.run(verify())
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
