"""samba_order.source_site → sales_channel_alias 백필 스크립트.

기존에 PlayAuto 임포트가 source_site 컬럼에 별칭("GS이숍(캐논)" 등)을 잘못 넣어둔 데이터를
새 sales_channel_alias 컬럼으로 이동한다.

규칙:
- source_site 에 '(' 가 포함된 행 → 그 값을 sales_channel_alias 로 복사
- source_site 는 '' 로 초기화 (소싱처는 collected_product 매칭으로 자연히 채워짐)
- 이미 sales_channel_alias 가 채워져 있으면 덮어쓰지 않음

실행 (VM):
  scp 로 컨테이너에 복사 → /app/backend/.venv/bin/python /tmp/backfill_sales_channel_alias.py

idempotent — 여러 번 실행해도 안전.
"""

import asyncio

import asyncpg


async def main() -> None:
    from backend.core.config import settings

    conn = await asyncpg.connect(
        host=settings.write_db_host,
        port=settings.write_db_port,
        user=settings.write_db_user,
        password=settings.write_db_password,
        database=settings.write_db_name,
        ssl=False,
    )
    try:
        # 1) 영향 받을 행 수 사전 점검
        total = await conn.fetchval(
            """
            SELECT COUNT(*) FROM samba_order
            WHERE source_site IS NOT NULL
              AND source_site <> ''
              AND position('(' in source_site) > 0
            """
        )
        print(f"[backfill] 별칭 형태 source_site 행 수: {total}")

        # 2) 이미 sales_channel_alias 가 빈 행만 이동
        moved = await conn.execute(
            """
            UPDATE samba_order
            SET sales_channel_alias = source_site,
                source_site = ''
            WHERE source_site IS NOT NULL
              AND source_site <> ''
              AND position('(' in source_site) > 0
              AND (sales_channel_alias IS NULL OR sales_channel_alias = '')
            """
        )
        print(f"[backfill] 이동 완료: {moved}")

        # 3) 잔존 ('(' 포함 source_site 가 비워졌는지 확인
        remain = await conn.fetchval(
            """
            SELECT COUNT(*) FROM samba_order
            WHERE source_site IS NOT NULL
              AND source_site <> ''
              AND position('(' in source_site) > 0
            """
        )
        print(f"[backfill] 잔존 별칭 source_site 행: {remain}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
