"""tracking 잡 owner_device_id 분포 (읽기 전용) — 컷오버 라우팅 검증용."""

import asyncio

from sqlalchemy import text

from backend.db.orm import get_read_session


async def main():
    async with get_read_session() as s:
        r = (
            await s.execute(
                text(
                    """
                    SELECT job_type, site,
                           coalesce(nullif(owner_device_id,''),'(empty/null)') AS owner,
                           status, count(*)
                    FROM samba_sourcing_job
                    WHERE job_type='tracking' AND created_at > now() - interval '3 days'
                    GROUP BY job_type, site, owner, status
                    ORDER BY count(*) DESC LIMIT 25
                    """
                )
            )
        ).fetchall()
        print("type | site | owner | status | cnt")
        for x in r:
            print(f"  {x[0]} | {x[1]} | {x[2][:34]} | {x[3]} | {x[4]}")


asyncio.run(main())
