"""최근 송장 잡 이력 — 사이트별 status 분포 + 최근 성공 시각 (읽기 전용)."""

import asyncio

from sqlalchemy import text

from backend.db.orm import get_read_session


async def main():
    async with get_read_session() as s:
        rows = (
            await s.execute(
                text(
                    """
                    SELECT sourcing_site, status, count(*) AS cnt,
                           max(scraped_at) AS last_scraped,
                           max(created_at) AS last_created
                    FROM samba_tracking_sync_job
                    WHERE created_at > now() - interval '14 days'
                    GROUP BY sourcing_site, status
                    ORDER BY sourcing_site, cnt DESC
                    """
                )
            )
        ).fetchall()
        print("=== 최근 14일 송장 잡 (site / status / 건수 / 최근스크랩 / 최근생성) ===")
        for r in rows:
            print(f"  {r[0]:<12} {r[1]:<18} {r[2]:>5}  scraped={r[3]} created={r[4]}")

        # 사이트별 최근 SCRAPED 성공 샘플
        print("\n=== 사이트별 최근 SCRAPED 성공 3건 ===")
        for site in ("LOTTEON", "SSG", "ABCMART", "MUSINSA"):
            srows = (
                await s.execute(
                    text(
                        """
                        SELECT sourcing_order_number, scraped_courier, scraped_tracking, scraped_at
                        FROM samba_tracking_sync_job
                        WHERE sourcing_site=:site AND status='SCRAPED'
                        ORDER BY scraped_at DESC NULLS LAST LIMIT 3
                        """
                    ),
                    {"site": site},
                )
            ).fetchall()
            print(f"  [{site}] {len(srows)}건")
            for r in srows:
                print(f"     ord={r[0]} courier={r[1]!r} track={r[2]!r} at={r[3]}")


asyncio.run(main())
