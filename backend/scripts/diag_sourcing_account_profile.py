"""소싱 계정 별 chrome_profile (owner_device_id 결정 키) 조회."""

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
        rows = await conn.fetch(
            """
            SELECT account_label, site_name, chrome_profile
            FROM samba_sourcing_account
            WHERE site_name ILIKE '%MUSINSA%' OR site_name = 'MUSINSA'
            ORDER BY account_label
            """
        )
        print("=== MUSINSA 소싱 계정 chrome_profile ===")
        for r in rows:
            print(
                f"  {r['account_label']:15s} chrome_profile={r['chrome_profile']!r}"
            )

        # 실제 잡들의 owner_device_id 분포
        print("\n=== 최근 7일 잡들의 owner_device_id (소싱계정별) ===")
        rows2 = await conn.fetch(
            """
            SELECT a.account_label, j.owner_device_id, j.status, COUNT(*) AS cnt
            FROM samba_tracking_sync_job j
            LEFT JOIN samba_sourcing_account a ON a.id = j.sourcing_account_id
            WHERE j.created_at >= now() - interval '7 days'
              AND j.sourcing_site = 'MUSINSA'
            GROUP BY a.account_label, j.owner_device_id, j.status
            ORDER BY a.account_label, j.status
            """
        )
        for r in rows2:
            print(
                f"  {r['account_label'] or '(none)':15s} owner={r['owner_device_id']!r:30s} "
                f"status={r['status']:18s} cnt={r['cnt']}"
            )
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
