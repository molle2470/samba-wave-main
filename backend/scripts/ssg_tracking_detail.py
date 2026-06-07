"""SSG 송장 실패 상세 진단 (읽기 전용)."""

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
        print("[A] 최근 1시간 SSG FAILED 잡 상세")
        rows = await conn.fetch(
            "SELECT id, sourcing_account_id, owner_device_id, attempts, "
            "left(last_error,60) e, "
            "to_char(updated_at AT TIME ZONE 'Asia/Seoul','MM-DD HH24:MI') t "
            "FROM samba_tracking_sync_job "
            "WHERE sourcing_site='SSG' AND status='FAILED' "
            "AND updated_at > now() - interval '1 hour' "
            "ORDER BY updated_at DESC LIMIT 20"
        )
        for r in rows:
            print(
                f"  {r['t']} acc={r['sourcing_account_id']} dev={str(r['owner_device_id'])[:10]} "
                f"att={r['attempts']} | {r['e']}"
            )

        print("\n[B] SSG 24시간 상태 추이 (시간대별)")
        rows = await conn.fetch(
            "SELECT to_char(date_trunc('hour', updated_at AT TIME ZONE 'Asia/Seoul'),"
            "'MM-DD HH24') h, status, count(*) c FROM samba_tracking_sync_job "
            "WHERE sourcing_site='SSG' AND updated_at > now() - interval '24 hours' "
            "GROUP BY h, status ORDER BY h DESC LIMIT 40"
        )
        for r in rows:
            print(f"  {r['h']}시 {r['status']:>15}: {r['c']}")

        print("\n[C] SSG 최근 성공(SENT_TO_MARKET) 있나 — 24h")
        ok = await conn.fetchrow(
            "SELECT count(*) c, max(updated_at AT TIME ZONE 'Asia/Seoul') last "
            "FROM samba_tracking_sync_job WHERE sourcing_site='SSG' "
            "AND status='SENT_TO_MARKET' AND updated_at > now() - interval '24 hours'"
        )
        print(f"  성공 {ok['c']}건, 마지막 성공: {ok['last']}")

        print("\n[D] SSG FAILED 'content script 응답 없음' 24h 추이")
        cs = await conn.fetchrow(
            "SELECT count(*) c, min(updated_at AT TIME ZONE 'Asia/Seoul') first, "
            "max(updated_at AT TIME ZONE 'Asia/Seoul') last "
            "FROM samba_tracking_sync_job WHERE sourcing_site='SSG' "
            "AND status='FAILED' AND last_error LIKE '%content script%' "
            "AND updated_at > now() - interval '24 hours'"
        )
        print(f"  24h content-script-timeout: {cs['c']}건 ({cs['first']} ~ {cs['last']})")

        print("\n[E] 실패 계정별 집계 24h (계정 잠금/특정계정 편중 판별)")
        rows = await conn.fetch(
            "SELECT sourcing_account_id, count(*) c FROM samba_tracking_sync_job "
            "WHERE sourcing_site='SSG' AND status='FAILED' "
            "AND updated_at > now() - interval '24 hours' "
            "GROUP BY sourcing_account_id ORDER BY c DESC"
        )
        for r in rows:
            print(f"  acc={r['sourcing_account_id']}: {r['c']}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
