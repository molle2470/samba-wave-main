"""SSG 송장 FAILED 잡 메타 — 언제 누가 처리/실패했나. read-only."""

import asyncio
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from backend.db.orm import get_read_session

_UTC = timezone.utc
_KST = timezone(timedelta(hours=9))


async def main() -> None:
    since = datetime.now(_UTC) - timedelta(days=8)
    async with get_read_session() as sess:
        # 컬럼 동적 확인
        cols = (
            await sess.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name='samba_tracking_sync_job' ORDER BY ordinal_position"
                )
            )
        ).scalars().all()
        print("컬럼:", cols)

        rows = (
            await sess.execute(
                text(
                    "SELECT id, sourcing_site, sourcing_order_number, status, attempts, "
                    "       last_error, owner_device_id, created_at, updated_at "
                    "FROM samba_tracking_sync_job "
                    "WHERE upper(coalesce(sourcing_site,''))='SSG' "
                    "  AND created_at >= :since "
                    "ORDER BY updated_at DESC"
                ),
                {"since": since},
            )
        ).mappings().all()
        print(f"\nSSG 송장 잡 (8일): {len(rows)}건")
        dist: dict[str, int] = {}
        for r in rows:
            dist[r["status"]] = dist.get(r["status"], 0) + 1
        print("상태분포:", json.dumps(dist, ensure_ascii=False))
        print("\n상세:")
        for r in rows:
            ua = r["updated_at"]
            ua_kst = ua.astimezone(_KST).strftime("%m-%d %H:%M:%S") if ua else "-"
            print(
                f"  {r['status']:14s} att={r['attempts']} dev={r['owner_device_id'] or '-'} "
                f"upd={ua_kst} ord={r['sourcing_order_number']} err={str(r['last_error'])[:70]!r}"
            )


if __name__ == "__main__":
    asyncio.run(main())
