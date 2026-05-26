"""프로덕션 SNKRDUNK 상품 옵션 실측 — VM 컨테이너에서 실행."""

import asyncio
import json

from sqlalchemy import text

from backend.db.orm import get_read_session


async def main():
    async with get_read_session() as s:
        total = (
            await s.execute(
                text(
                    "SELECT count(*) FROM samba_collected_product "
                    "WHERE source_site='SNKRDUNK'"
                )
            )
        ).scalar()
        print(f"SNKRDUNK total={total}")

        rows = (
            await s.execute(
                text(
                    """
            SELECT
              CASE
                WHEN options IS NULL THEN 'null'
                WHEN jsonb_typeof(options)='array' AND jsonb_array_length(options)=0 THEN '0'
                WHEN jsonb_typeof(options)='array' AND jsonb_array_length(options)=1 THEN '1'
                ELSE '2+'
              END AS bucket,
              count(*) AS c
            FROM samba_collected_product
            WHERE source_site='SNKRDUNK'
            GROUP BY 1 ORDER BY 1
            """
                )
            )
        ).fetchall()
        print("옵션 개수 분포:")
        for r in rows:
            print(f"  {r[0]}: {r[1]}")

        samples = (
            await s.execute(
                text(
                    """
            SELECT site_product_id, name, sale_price, original_price, sale_status,
                   options, extra_data, last_refreshed_at
            FROM samba_collected_product
            WHERE source_site='SNKRDUNK'
            ORDER BY last_refreshed_at DESC NULLS LAST
            LIMIT 12
            """
                )
            )
        ).fetchall()
        print("\n샘플(최근갱신순):")
        for s2 in samples:
            opts = s2[5]
            if isinstance(opts, str):
                try:
                    opts = json.loads(opts)
                except Exception:
                    pass
            ed = s2[6]
            if isinstance(ed, str):
                try:
                    ed = json.loads(ed)
                except Exception:
                    ed = {}
            snkr_type = (ed or {}).get("snkr_type")
            n_opts = len(opts) if isinstance(opts, list) else 0
            print(
                f"  id={s2[0]} type={snkr_type} sale={s2[2]} orig={s2[3]} "
                f"status={s2[4]} n_opts={n_opts} refreshed={s2[7]}"
            )
            if isinstance(opts, list) and opts:
                print(f"     opts[:3]={opts[:3]}")


asyncio.run(main())
