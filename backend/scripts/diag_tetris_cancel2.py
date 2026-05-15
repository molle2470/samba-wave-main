"""테트리스 OFF 토글 후속 진단 — CANCELLED 이력 추적."""

import asyncio

import asyncpg

from backend.core.config import settings


async def main() -> None:
    conn = await asyncpg.connect(
        host="172.18.0.2",
        port=5432,
        user=settings.read_db_user,
        password=settings.read_db_password,
        database=settings.read_db_name,
        ssl=False,
    )

    # 1) 최근 7일 transmit 잡 CANCELLED 이력
    rows = await conn.fetch(
        """
        SELECT
          date_trunc('hour', completed_at) AS hr,
          COALESCE((payload::jsonb)->>'origin', '(없음)') AS origin,
          COUNT(*) AS cnt
        FROM samba_jobs
        WHERE job_type = 'transmit'
          AND status = 'cancelled'
          AND completed_at > now() - interval '7 day'
        GROUP BY hr, origin
        ORDER BY hr DESC
        LIMIT 30
        """
    )
    print("[최근 7d CANCELLED transmit 잡 — 시각별]")
    for r in rows:
        print(f"  {r['hr']} | origin={r['origin']:20s} | {r['cnt']:5d}건")
    print()

    # 2) 오토튠 상태 (혹시 비상정지/오토튠 매개로 됐을 수 있음)
    rows = await conn.fetch(
        "SELECT key, value, updated_at FROM samba_settings "
        "WHERE key IN ('tetris_sync_interval_hours', 'emergency_stop', "
        "'autotune_running', 'global_pause') ORDER BY updated_at DESC"
    )
    print("[관련 settings]")
    for r in rows:
        print(f"  {r['key']:35s} = {r['value']:10s} (updated={r['updated_at']})")
    print()

    # 3) 가장 오래된 PENDING 잡 — 큐 적체 진단
    rows = await conn.fetch(
        """
        SELECT id, status, created_at, started_at,
               (payload::jsonb)->>'source_site' AS site,
               (payload::jsonb)->>'brand_name' AS brand,
               jsonb_array_length(COALESCE((payload::jsonb)->'product_ids', '[]'::jsonb)) AS pid_cnt
        FROM samba_jobs
        WHERE job_type = 'transmit'
          AND status = 'pending'
        ORDER BY created_at ASC
        LIMIT 5
        """
    )
    print("[가장 오래된 PENDING transmit 5건]")
    for r in rows:
        print(
            f"  {r['id'][:12]} {r['status']:8s} created={r['created_at']} "
            f"site={r['site']}/{r['brand']} pids={r['pid_cnt']}"
        )
    print()

    # 4) 잡 상태 변화 추적 — updated_at 또는 completed_at 기준
    rows = await conn.fetch(
        """
        SELECT
          status, COUNT(*) AS cnt
        FROM samba_jobs
        WHERE job_type = 'transmit'
          AND created_at > now() - interval '24 hour'
        GROUP BY status
        ORDER BY status
        """
    )
    print("[최근 24h 생성된 transmit 잡 — 현재 상태]")
    for r in rows:
        print(f"  {r['status']:12s} | {r['cnt']:5d}건")

    await conn.close()


asyncio.run(main())
