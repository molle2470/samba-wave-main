"""SSG 송장 실패 판별 (읽기전용): 코드버그(전역) vs 계정잠금(디바이스/계정 국한)."""

import asyncio

import asyncpg

from backend.core.config import settings


async def main() -> None:
    conn = await asyncpg.connect(
        host="172.18.0.2",
        port=5432,
        user=settings.write_db_user,
        password=settings.write_db_password,
        database=settings.write_db_name,
        ssl=False,
    )
    try:
        print("[A] 7일 SSG 디바이스별 상태 — 다른 디바이스서 성공하나?")
        rows = await conn.fetch(
            "SELECT owner_device_id, status, count(*) c FROM samba_tracking_sync_job "
            "WHERE sourcing_site='SSG' AND created_at > now() - interval '7 days' "
            "GROUP BY owner_device_id, status ORDER BY owner_device_id, c DESC"
        )
        for r in rows:
            print(f"  dev={str(r['owner_device_id'])[:14]:>14} {r['status']:>15}: {r['c']}")

        print("\n[B] SSG 마지막 성공 시각 (전체)")
        last = await conn.fetchrow(
            "SELECT max(updated_at AT TIME ZONE 'Asia/Seoul') l "
            "FROM samba_tracking_sync_job WHERE sourcing_site='SSG' "
            "AND status='SENT_TO_MARKET'"
        )
        print(f"  마지막 SSG 성공: {last['l']}")

        print("\n[C] SSG last_error 종류별 7일 (계정잠금 시그니처 탐지)")
        rows = await conn.fetch(
            "SELECT left(last_error, 50) e, count(*) c FROM samba_tracking_sync_job "
            "WHERE sourcing_site='SSG' AND status='FAILED' "
            "AND created_at > now() - interval '7 days' "
            "GROUP BY left(last_error,50) ORDER BY c DESC LIMIT 15"
        )
        for r in rows:
            print(f"  {r['c']:>4}x  {r['e']}")

        print("\n[D] 비교: 다른 데몬사이트(ABCmart/LOTTEON) 최근 24h 성공률")
        rows = await conn.fetch(
            "SELECT sourcing_site, status, count(*) c FROM samba_tracking_sync_job "
            "WHERE sourcing_site IN ('ABCmart','GrandStage','LOTTEON','MUSINSA') "
            "AND updated_at > now() - interval '24 hours' "
            "GROUP BY sourcing_site, status ORDER BY sourcing_site, c DESC"
        )
        for r in rows:
            print(f"  {r['sourcing_site']:>10} {r['status']:>15}: {r['c']}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
