"""오토튠 작동 상태 진단 (읽기 전용).

PC↔사이트 배정 + 실제 처리 최신도를 프로덕션 DB로 확인.
기대: PC1=MUSINSA/GSShop/ABCmart, PC2=LOTTEON, PC3=SSG.
"""

import asyncio

from sqlalchemy import text

from backend.db.orm import get_read_session


async def main():
    async with get_read_session() as s:
        # ① 최근 30분 잡큐: 사이트 × owner_device_id 별 상태/최신시각
        print("=" * 78)
        print("[1] 최근 30분 소싱 잡큐 (site × owner_device_id)")
        print("=" * 78)
        rows = (
            await s.execute(
                text(
                    """
                    SELECT site,
                           COALESCE(owner_device_id, '(none)') AS dev,
                           job_type,
                           status,
                           count(*) AS cnt,
                           max(created_at)    AS last_created,
                           max(dispatched_at) AS last_dispatched,
                           max(completed_at)  AS last_completed
                    FROM samba_sourcing_job
                    WHERE created_at > now() - interval '30 minutes'
                    GROUP BY site, owner_device_id, job_type, status
                    ORDER BY site, dev, job_type, status
                    """
                )
            )
        ).fetchall()
        if not rows:
            print("  (최근 30분 잡 없음)")
        for r in rows:
            site, dev, jt, st, cnt, lc, ld, lcomp = r
            dev_s = (dev[:14] + "..") if len(dev) > 16 else dev
            print(
                f"  {str(site):<9} | dev={dev_s:<18} | {str(jt):<14} | "
                f"{str(st):<10} | cnt={cnt:<4} | disp={ld} | comp={lcomp}"
            )

        # ② 사이트별 owner_device_id 요약 (배정 한눈에)
        print()
        print("=" * 78)
        print("[2] 사이트별 owner_device_id (최근 30분, dispatched/completed 기준)")
        print("=" * 78)
        rows2 = (
            await s.execute(
                text(
                    """
                    SELECT site,
                           COALESCE(owner_device_id,'(none)') AS dev,
                           count(*) AS cnt,
                           max(GREATEST(
                               COALESCE(dispatched_at, 'epoch'::timestamptz),
                               COALESCE(completed_at,  'epoch'::timestamptz)
                           )) AS last_active
                    FROM samba_sourcing_job
                    WHERE created_at > now() - interval '30 minutes'
                    GROUP BY site, owner_device_id
                    ORDER BY site, cnt DESC
                    """
                )
            )
        ).fetchall()
        cur = None
        for r in rows2:
            site, dev, cnt, la = r
            if site != cur:
                print(f"\n  ── {site} ──")
                cur = site
            dev_s = (dev[:20] + "..") if len(dev) > 22 else dev
            print(f"     dev={dev_s:<24} cnt={cnt:<5} last_active={la}")

        # ③ 상품 갱신 최신도 (실제 처리 ground truth)
        print()
        print("=" * 78)
        print("[3] source_site 별 상품 갱신 최신도 (samba_collected_product)")
        print("=" * 78)
        rows3 = (
            await s.execute(
                text(
                    """
                    SELECT source_site,
                           count(*) FILTER (
                               WHERE last_refreshed_at > now() - interval '10 minutes'
                           ) AS r10m,
                           count(*) FILTER (
                               WHERE last_refreshed_at > now() - interval '1 hour'
                           ) AS r1h,
                           count(*) FILTER (
                               WHERE last_refreshed_at > now() - interval '24 hours'
                           ) AS r24h,
                           max(last_refreshed_at) AS last_refresh,
                           count(*) AS total
                    FROM samba_collected_product
                    GROUP BY source_site
                    ORDER BY r1h DESC
                    """
                )
            )
        ).fetchall()
        print(
            f"  {'source_site':<14} | {'10분':>6} | {'1시간':>6} | "
            f"{'24시간':>7} | {'등록상품':>8} | last_refresh"
        )
        print("  " + "-" * 74)
        for r in rows3:
            ss, r10m, r1h, r24h, last, total = r
            print(
                f"  {str(ss):<14} | {r10m:>6} | {r1h:>6} | {r24h:>7} | "
                f"{total:>8} | {last}"
            )

        # ④ 최근 1시간 오토튠 monitor 이벤트 (변동 감지 = 실제 작동 증거)
        print()
        print("=" * 78)
        print("[4] 최근 1시간 오토튠 변동 이벤트 (samba_monitor_event)")
        print("=" * 78)
        rows4 = (
            await s.execute(
                text(
                    """
                    SELECT source_site, event_type, count(*) AS cnt,
                           max(created_at) AS last_evt
                    FROM samba_monitor_event
                    WHERE created_at > now() - interval '1 hour'
                    GROUP BY source_site, event_type
                    ORDER BY source_site, cnt DESC
                    """
                )
            )
        ).fetchall()
        if not rows4:
            print("  (최근 1시간 이벤트 없음)")
        for r in rows4:
            ss, et, cnt, last = r
            print(f"  {str(ss):<12} | {str(et):<22} | cnt={cnt:<5} | last={last}")

        # ⑤ 사이트별 마지막 detail(=오토튠 가격/재고) 잡 시각 (6시간 창)
        print()
        print("=" * 78)
        print("[5] 사이트별 마지막 detail 잡 (6시간 창, MUSINSA/GSShop 추적)")
        print("=" * 78)
        rows5 = (
            await s.execute(
                text(
                    """
                    SELECT site,
                           count(*) AS cnt6h,
                           max(created_at)    AS last_created,
                           max(completed_at)  AS last_completed
                    FROM samba_sourcing_job
                    WHERE job_type = 'detail'
                      AND created_at > now() - interval '6 hours'
                    GROUP BY site
                    ORDER BY last_created DESC NULLS LAST
                    """
                )
            )
        ).fetchall()
        for r in rows5:
            site, cnt, lc, lcomp = r
            print(
                f"  {str(site):<10} | cnt6h={cnt:<5} | "
                f"last_created={lc} | last_completed={lcomp}"
            )

        print()
        print("[서버시각] ", end="")
        now_row = (await s.execute(text("SELECT now()"))).scalar()
        print(now_row)


asyncio.run(main())
