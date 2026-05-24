"""GSShop 6개 타깃의 오토튠 scope 여부 진단 — 등록/정책/상태/갱신시각."""

import asyncio
import asyncpg
from backend.core.config import settings

TARGETS = [
    "1114482436",
    "1117278628",
    "1109875187",
    "1112124919",
    "1112295610",
    "1109875192",
]


async def main():
    conn = await asyncpg.connect(
        host="172.18.0.2",
        port=5432,
        user=settings.read_db_user,
        password=settings.read_db_password,
        database=settings.read_db_name,
        ssl=False,
    )
    try:
        rows = await conn.fetch(
            """
            SELECT site_product_id, sale_status, applied_policy_id,
                   registered_accounts::text AS reg_txt,
                   options::text AS opt_txt,
                   last_refreshed_at
            FROM samba_collected_product
            WHERE source_site='GSShop' AND site_product_id = ANY($1::text[])
            ORDER BY site_product_id
            """,
            TARGETS,
        )
        print("[scope] GSShop 6개 타깃 DB 상태:", flush=True)
        for r in rows:
            reg = r["reg_txt"] or "[]"
            opt = r["opt_txt"] or "[]"
            reg_short = reg if len(reg) < 80 else reg[:80] + "..."
            print(
                f"  sid={r['site_product_id']} status={r['sale_status']} "
                f"policy={'Y' if r['applied_policy_id'] else 'N'} "
                f"opt_chars={len(opt)} last_refreshed={r['last_refreshed_at']}\n"
                f"      reg={reg_short}",
                flush=True,
            )
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
