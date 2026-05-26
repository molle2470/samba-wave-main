"""LOTTEON 잡 result에서 login_required=true 카운트 (진짜 실패 추적)."""

import asyncio

from sqlalchemy import text

from backend.db.orm import get_read_session


async def main():
    async with get_read_session() as s:
        print("=== 최근 30분 LOTTEON 잡 result.login_required 분포 ===")
        r = (
            await s.execute(
                text(
                    """
                    SELECT
                      coalesce(nullif(owner_device_id,''),'(empty)') AS owner,
                      (result->>'login_required')::text AS login_req,
                      (result->>'success')::text AS success,
                      count(*) AS cnt
                    FROM samba_sourcing_job
                    WHERE site='LOTTEON' AND job_type='detail'
                      AND status='completed'
                      AND completed_at > now() - interval '30 minutes'
                    GROUP BY owner, login_req, success
                    ORDER BY cnt DESC
                    """
                )
            )
        ).fetchall()
        for x in r:
            print(
                f"  owner={x[0]:<45} login_required={x[1]:<5} success={x[2]:<5} cnt={x[3]}"
            )

        print("\n=== 최근 LOTTEON login_required 잡 1건 result 전체 ===")
        r2 = (
            await s.execute(
                text(
                    """
                    SELECT request_id, owner_device_id, result, completed_at
                    FROM samba_sourcing_job
                    WHERE site='LOTTEON' AND job_type='detail'
                      AND (result->>'login_required')::text = 'true'
                      AND completed_at > now() - interval '30 minutes'
                    ORDER BY completed_at DESC LIMIT 1
                    """
                )
            )
        ).fetchone()
        if r2:
            print(f"  request_id={r2[0]}")
            print(f"  owner={r2[1]}")
            print(f"  completed_at={r2[3]}")
            print(f"  result(앞400자)={str(r2[2])[:400]}")
        else:
            print("  (최근 30분 LOTTEON login_required 잡 없음)")


asyncio.run(main())
