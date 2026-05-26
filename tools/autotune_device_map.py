"""device_id ↔ 사이트 처리/만료 매핑 (읽기 전용).

목적: 같은 사이트를 여러 device가 동시 소유(중복 루프)하는지,
각 device가 실제로 잡을 완료하는지 vs 만료시키는지 확인.
"""

import asyncio

from sqlalchemy import text

from backend.db.orm import get_read_session


async def main():
    async with get_read_session() as s:
        # device_id × site × status 최근 2시간 (detail 잡만)
        print("=" * 90)
        print("[A] device_id × site × status — 최근 2시간 detail 잡")
        print("=" * 90)
        rows = (
            await s.execute(
                text(
                    """
                    SELECT COALESCE(owner_device_id,'(none)') AS dev,
                           site,
                           status,
                           count(*) AS cnt,
                           max(completed_at) AS last_comp,
                           max(created_at)   AS last_created
                    FROM samba_sourcing_job
                    WHERE job_type='detail'
                      AND created_at > now() - interval '2 hours'
                    GROUP BY owner_device_id, site, status
                    ORDER BY site, dev, status
                    """
                )
            )
        ).fetchall()
        cur = None
        for r in rows:
            dev, site, st, cnt, lc, lcr = r
            if site != cur:
                print(f"\n  ══ {site} ══")
                cur = site
            print(
                f"    {dev:<28} {st:<11} cnt={cnt:<5} "
                f"last_comp={lc} created={lcr}"
            )

        # 등록 device 목록 (extension_key) — label/last_seen
        print()
        print("=" * 90)
        print("[B] 등록 device (samba_extension_key) — label / device_id / last_seen")
        print("=" * 90)
        try:
            rows2 = (
                await s.execute(
                    text(
                        """
                        SELECT device_id, label, created_at, revoked_at
                        FROM samba_extension_key
                        WHERE device_id IS NOT NULL
                        ORDER BY created_at DESC NULLS LAST
                        LIMIT 30
                        """
                    )
                )
            ).fetchall()
            for r in rows2:
                did, label, created, revoked = r
                rv = " [REVOKED]" if revoked else ""
                print(f"  {str(did):<34} | {str(label):<24} | created={created}{rv}")
        except Exception as e:
            print(f"  (조회 실패: {e})")


asyncio.run(main())
