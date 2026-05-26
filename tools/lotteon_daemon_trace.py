"""LOTTEON 잡이 어느 데몬에 라우팅되고 어떤 결과 나왔는지 추적 (읽기 전용)."""

import asyncio

from sqlalchemy import text

from backend.db.orm import get_read_session


async def main():
    async with get_read_session() as s:
        print("=== 최근 30분 LOTTEON detail 잡 owner + status 분포 ===")
        r = (
            await s.execute(
                text(
                    """
                    SELECT
                      coalesce(nullif(owner_device_id,''),'(empty)') AS owner,
                      status,
                      count(*) AS cnt,
                      max(created_at) AS last_created
                    FROM samba_sourcing_job
                    WHERE site='LOTTEON' AND job_type='detail'
                      AND created_at > now() - interval '30 minutes'
                    GROUP BY owner, status
                    ORDER BY cnt DESC LIMIT 30
                    """
                )
            )
        ).fetchall()
        for x in r:
            print(f"  owner={x[0]:<45} status={x[1]:<12} cnt={x[2]:<6} last={x[3]}")

        print("\n=== 최근 LOTTEON 실패 1건 result 본문 ===")
        r2 = (
            await s.execute(
                text(
                    """
                    SELECT owner_device_id, status, result, error, completed_at
                    FROM samba_sourcing_job
                    WHERE site='LOTTEON' AND status='failed'
                      AND created_at > now() - interval '30 minutes'
                    ORDER BY created_at DESC LIMIT 1
                    """
                )
            )
        ).fetchone()
        if r2:
            print(f"  owner={r2[0]} status={r2[1]} completed_at={r2[4]}")
            print(f"  error={r2[3]}")
            print(f"  result={str(r2[2])[:400]}")
        else:
            print("  (최근 30분 LOTTEON 실패 잡 없음)")

        print(
            "\n=== 활성 데몬 device_id + last_seen (HTTP /autotune-daemon/health 대체) ==="
        )
        # _pc_last_seen 은 다른 프로세스 메모리. DB samba_chrome_profile 대신 sourcing_job 로 도출
        r3 = (
            await s.execute(
                text(
                    """
                    SELECT owner_device_id, count(*), max(dispatched_at), max(completed_at)
                    FROM samba_sourcing_job
                    WHERE owner_device_id LIKE 'samba-daemon-%'
                      AND created_at > now() - interval '1 hour'
                    GROUP BY owner_device_id
                    ORDER BY max(dispatched_at) DESC NULLS LAST
                    """
                )
            )
        ).fetchall()
        if not r3:
            print("  (최근 1시간 samba-daemon-* device 활동 없음)")
        for x in r3:
            print(
                f"  {x[0]:<45} jobs={x[1]:<6} last_dispatched={x[2]} last_completed={x[3]}"
            )


asyncio.run(main())
