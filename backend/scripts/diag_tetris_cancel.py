"""테트리스 OFF 토글 후 transmit 잡 상태 진단.

VM 컨테이너에서 실행:
  sudo docker cp diag_tetris_cancel.py samba-samba-api-1:/tmp/diag_tetris_cancel.py
  sudo docker exec samba-samba-api-1 /app/backend/.venv/bin/python /tmp/diag_tetris_cancel.py
"""

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

    # 1) tetris_sync_interval_hours 설정값
    row = await conn.fetchrow(
        "SELECT value FROM samba_settings WHERE key = 'tetris_sync_interval_hours'"
    )
    print(f"[설정] tetris_sync_interval_hours = {row['value'] if row else 'NULL'}")
    print()

    # 2) 현재 PENDING/RUNNING transmit 잡 상태 분포 (bounded set)
    rows = await conn.fetch(
        """
        SELECT
          status,
          COALESCE(payload->>'origin', '(없음)') AS origin,
          COUNT(*) AS cnt
        FROM samba_jobs
        WHERE job_type = 'transmit'
          AND status IN ('pending', 'running')
        GROUP BY status, origin
        ORDER BY status, origin
        """
    )
    print("[현재 PENDING/RUNNING transmit 잡 분포]")
    for r in rows:
        print(f"  {r['status']:12s} | origin={r['origin']:20s} | {r['cnt']:5d}건")
    print()

    # 3) PENDING/RUNNING 으로 살아있는 transmit 잡 상세
    rows = await conn.fetch(
        """
        SELECT
          id,
          status,
          COALESCE(payload->>'origin', '(없음)') AS origin,
          payload->>'source_site' AS site,
          payload->>'brand_name' AS brand,
          payload->>'target_account_ids' AS accts,
          jsonb_array_length(COALESCE((payload::jsonb)->'product_ids', '[]'::jsonb)) AS pid_cnt,
          created_at,
          started_at
        FROM samba_jobs
        WHERE job_type = 'transmit'
          AND status IN ('pending', 'running')
        ORDER BY created_at DESC
        LIMIT 30
        """
    )
    print(f"[현재 살아있는 PENDING/RUNNING transmit 잡 — {len(rows)}건]")
    for r in rows:
        print(
            f"  {r['id'][:8]} {r['status']:8s} origin={r['origin']:14s} "
            f"{r['site']}/{r['brand']} accts={r['accts']} pids={r['pid_cnt']} "
            f"created={r['created_at']:%H:%M:%S} started={r['started_at']}"
        )
    print()

    # 4) origin 마커 누락된 transmit 잡 카운트 (가설 1 핵심)
    row = await conn.fetchrow(
        """
        SELECT COUNT(*) AS cnt
        FROM samba_jobs
        WHERE job_type = 'transmit'
          AND status IN ('pending', 'running')
          AND (payload->>'origin') IS DISTINCT FROM 'tetris_sync'
        """
    )
    print(
        f"[가설 1 검증] origin != 'tetris_sync' 인 PENDING/RUNNING transmit 잡: "
        f"{row['cnt']}건"
    )
    print("  → 이 값이 0이 아니면 cancel_pending_tetris_jobs 필터가 누락하는 잡 존재")
    print()

    # 5) 최근 2시간 CANCELLED transmit 잡 — 토글 OFF 효과 확인
    rows = await conn.fetch(
        """
        SELECT
          COALESCE(payload->>'origin', '(없음)') AS origin,
          COUNT(*) AS cnt
        FROM samba_jobs
        WHERE job_type = 'transmit'
          AND status = 'cancelled'
          AND completed_at > now() - interval '2 hour'
        GROUP BY origin
        ORDER BY origin
        """
    )
    print("[최근 2h CANCELLED transmit 잡 — origin별]")
    for r in rows:
        print(f"  origin={r['origin']:20s} | {r['cnt']:5d}건")

    await conn.close()


asyncio.run(main())
