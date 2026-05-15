"""25 처리 후 멈춘 PENDING 잡 진단 — 왜 처리 안 됐는지."""

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
        # 현재 모달 표시 대상 잡들의 분포
        rows = await conn.fetch(
            """
            SELECT
              j.status,
              COUNT(*) AS cnt
            FROM samba_tracking_sync_job j
            JOIN samba_order o ON o.id = j.order_id
            WHERE COALESCE(o.paid_at, o.created_at) >= now() - interval '8 days'
            GROUP BY j.status
            ORDER BY cnt DESC
            """
        )
        print("=== 최근 8일 잡 상태 분포 ===")
        for r in rows:
            print(f"  {r['status']:20s} {r['cnt']:5d}")

        # PENDING 잡의 owner_device_id / expires_at / created_at 확인
        rows2 = await conn.fetch(
            """
            SELECT
              j.id AS job_id,
              j.order_id,
              o.order_number,
              j.owner_device_id,
              j.request_id,
              j.created_at AS job_created_at,
              j.updated_at AS job_updated_at,
              sq.expires_at,
              sq.status AS sq_status,
              CASE WHEN sq.expires_at IS NULL THEN 'no_queue_row'
                   WHEN sq.expires_at < now() THEN 'EXPIRED'
                   ELSE 'live' END AS expiry_state
            FROM samba_tracking_sync_job j
            JOIN samba_order o ON o.id = j.order_id
            LEFT JOIN samba_sourcing_job sq ON sq.request_id = j.request_id
            WHERE j.status = 'PENDING'
              AND COALESCE(o.paid_at, o.created_at) >= now() - interval '8 days'
            ORDER BY j.created_at DESC
            LIMIT 30
            """
        )
        print(f"\n=== PENDING 잡 상세 ({len(rows2)}건) ===")
        for r in rows2:
            print(
                f"  {r['order_number']:25s} "
                f"req={r['request_id']!r:12s} "
                f"owner={r['owner_device_id']!r:20s} "
                f"job_created={r['job_created_at']!s:30s} "
                f"sq_status={r['sq_status']!r:12s} "
                f"expires={r['expires_at']!s:30s} "
                f"state={r['expiry_state']}"
            )
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
