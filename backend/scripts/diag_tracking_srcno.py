"""송장 잡 사이트별 sourcing_order_number 누락 진단."""

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
        # 전체 상태 분포
        status_rows = await conn.fetch(
            """
            SELECT status, COUNT(*) AS cnt
            FROM samba_tracking_sync_job
            GROUP BY status
            ORDER BY cnt DESC
            """
        )
        print("=== 상태별 잡 수 ===")
        for r in status_rows:
            print(f"  {r['status']:20s} {r['cnt']:5d}")
        print()
        # 사이트별 분포 (모든 상태)
        rows = await conn.fetch(
            """
            SELECT sourcing_site, status,
                   COUNT(*) AS total,
                   COUNT(CASE WHEN sourcing_order_number IS NULL OR sourcing_order_number = '' THEN 1 END) AS empty_srcno
            FROM samba_tracking_sync_job
            GROUP BY sourcing_site, status
            ORDER BY total DESC
            LIMIT 30
            """
        )
        print("=== 사이트×상태별 srcno 누락 ===")
        for r in rows:
            site = r["sourcing_site"] or "(빈값)"
            print(
                f"  {site:25s} {r['status']:15s} total={r['total']:5d} empty={r['empty_srcno']:5d}"
            )
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
