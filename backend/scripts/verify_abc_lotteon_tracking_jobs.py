"""ABC/LOTTEON 송장 잡 상태 + default 계정 — read-only. SSG 동일 버그 있나 진단."""

import asyncio
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from backend.db.orm import get_read_session

_KST = timezone(timedelta(hours=9))


async def main() -> None:
    since = datetime.now(timezone.utc) - timedelta(days=8)
    async with get_read_session() as sess:
        for site in ("ABCmart", "GrandStage", "LOTTEON"):
            rows = (
                await sess.execute(
                    text(
                        "SELECT status, count(*) c FROM samba_tracking_sync_job "
                        "WHERE upper(coalesce(sourcing_site,''))=upper(:s) AND created_at>=:since "
                        "GROUP BY status ORDER BY c DESC"
                    ),
                    {"s": site, "since": since},
                )
            ).all()
            dist = {r[0]: r[1] for r in rows}
            errs = (
                await sess.execute(
                    text(
                        "SELECT last_error, count(*) c FROM samba_tracking_sync_job "
                        "WHERE upper(coalesce(sourcing_site,''))=upper(:s) AND created_at>=:since "
                        "AND status='FAILED' AND last_error IS NOT NULL "
                        "GROUP BY last_error ORDER BY c DESC LIMIT 3"
                    ),
                    {"s": site, "since": since},
                )
            ).all()
            print(f"\n[{site}] 8일 송장잡 상태: {json.dumps(dist, ensure_ascii=False)}")
            for e, c in errs:
                print(f"   FAILED×{c}: {str(e)[:90]!r}")

        print("\n=== ABC/LOTTEON default 계정 ===")
        accs = (
            await sess.execute(
                text(
                    "SELECT site_name, account_label, username, "
                    "(password IS NOT NULL AND password<>'') has_pw, is_login_default "
                    "FROM samba_sourcing_account "
                    "WHERE upper(site_name) IN ('ABCMART','GRANDSTAGE','LOTTEON') "
                    "ORDER BY site_name, is_login_default DESC"
                )
            )
        ).all()
        for s, lbl, u, pw, dflt in accs:
            print(f"   {s} label={lbl!r} user4={(u or '')[:4]}*** has_pw={pw} default={dflt}")


if __name__ == "__main__":
    asyncio.run(main())
