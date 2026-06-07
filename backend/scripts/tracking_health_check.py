"""송장수집 건강 점검 (읽기 전용) — 최근 1시간 tracking 잡 사이트별 상태.

정상(미발송 포함)은 제외하고 진짜 실패만 강조:
  FAILED(추출에러) / WRONG_ACCOUNT(계정불일치) / DISPATCHED-stuck(30분+) / DISPATCH_FAILED
"""

import asyncio
from collections import defaultdict

import asyncpg

from backend.core.config import settings

SITES = ("MUSINSA", "SSG", "ABCmart", "GrandStage", "LOTTEON")
REAL_FAIL = ("FAILED", "WRONG_ACCOUNT", "DISPATCH_FAILED")


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
        print("=" * 64)
        print("[1] 최근 1시간 사이트 x 상태 (created/updated 기준)")
        rows = await conn.fetch(
            "SELECT sourcing_site, status, count(*) c FROM samba_tracking_sync_job "
            "WHERE created_at > now() - interval '1 hour' "
            "OR updated_at > now() - interval '1 hour' "
            "GROUP BY sourcing_site, status ORDER BY sourcing_site, c DESC"
        )
        by_site: dict = defaultdict(dict)
        for r in rows:
            by_site[r["sourcing_site"]][r["status"]] = r["c"]
        if not rows:
            print("  최근 1시간 tracking 잡 없음")
        for site in sorted(by_site):
            stats = by_site[site]
            disp = " ".join(f"{k}={v}" for k, v in stats.items())
            print(f"  {site:>12}: {disp}")

        print("=" * 64)
        print("[2] 진짜 실패 카운트 (정상/미발송/선물 제외)")
        total_real = 0
        # FAILED / WRONG_ACCOUNT / DISPATCH_FAILED (최근 1시간)
        fr = await conn.fetch(
            "SELECT sourcing_site, status, count(*) c FROM samba_tracking_sync_job "
            "WHERE status = ANY($1::text[]) "
            "AND updated_at > now() - interval '1 hour' "
            "GROUP BY sourcing_site, status ORDER BY c DESC",
            list(REAL_FAIL),
        )
        for r in fr:
            total_real += r["c"]
            print(f"  {r['sourcing_site']:>12} {r['status']:>15}: {r['c']}")
        # DISPATCHED stuck — 확장앱이 잡 받고 30분+ 안 끝냄
        stuck = await conn.fetch(
            "SELECT sourcing_site, count(*) c FROM samba_tracking_sync_job "
            "WHERE status = 'DISPATCHED' AND updated_at < now() - interval '30 minutes' "
            "AND updated_at > now() - interval '6 hours' "
            "GROUP BY sourcing_site ORDER BY c DESC"
        )
        for r in stuck:
            total_real += r["c"]
            print(f"  {r['sourcing_site']:>12} {'DISPATCHED-stuck':>15}: {r['c']}")
        print(f"\n  >>> 진짜 실패 합계: {total_real}")

        print("=" * 64)
        print("[3] 실패 last_error 샘플 (최근 1시간, 최대 12건)")
        er = await conn.fetch(
            "SELECT sourcing_site, status, left(last_error, 110) e, updated_at "
            "FROM samba_tracking_sync_job "
            "WHERE status = ANY($1::text[]) AND last_error IS NOT NULL "
            "AND updated_at > now() - interval '1 hour' "
            "ORDER BY updated_at DESC LIMIT 12",
            list(REAL_FAIL),
        )
        for r in er:
            print(f"  [{r['sourcing_site']}/{r['status']}] {r['e']}")

        print("=" * 64)
        print("[4] 참고: 정상 분류 건수 (제외 대상)")
        norm = await conn.fetch(
            "SELECT status, count(*) c FROM samba_tracking_sync_job "
            "WHERE status IN ('NO_TRACKING','CANCELLED','SENT_TO_MARKET','SCRAPED','PENDING') "
            "AND (created_at > now() - interval '1 hour' "
            "OR updated_at > now() - interval '1 hour') GROUP BY status ORDER BY c DESC"
        )
        for r in norm:
            print(f"  {r['status']:>16}: {r['c']}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
