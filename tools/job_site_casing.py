"""samba_sourcing_job 의 job_type/site 조합 + payload site 케이싱 확인 (읽기 전용)."""

import asyncio

from sqlalchemy import text

from backend.db.orm import get_read_session


async def main():
    async with get_read_session() as s:
        rows = (
            await s.execute(
                text(
                    """
                    SELECT job_type, site,
                           payload->>'site' AS payload_site,
                           count(*) AS cnt
                    FROM samba_sourcing_job
                    WHERE created_at > now() - interval '7 days'
                    GROUP BY job_type, site, payload->>'site'
                    ORDER BY job_type, cnt DESC
                    """
                )
            )
        ).fetchall()
        print("job_type | site(col) | payload.site | cnt")
        for r in rows:
            print(f"  {r[0]:<10} | {str(r[1]):<14} | {str(r[2]):<14} | {r[3]}")


asyncio.run(main())
