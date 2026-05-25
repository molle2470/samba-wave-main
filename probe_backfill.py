"""백필 결과 검증 — samba_market_account default 행 + samba_settings.store_* 잔존 비교."""

import asyncio
import asyncpg
from backend.core.config import settings


async def main():
    c = await asyncpg.connect(
        host="172.18.0.2",
        port=5432,
        user=settings.read_db_user,
        password=settings.read_db_password,
        database=settings.read_db_name,
        ssl=False,
    )

    print("=" * 80)
    print("[1] samba_market_account — is_default=True 행 (마켓별)")
    print("=" * 80)
    rows = await c.fetch(
        """
        SELECT market_type, COUNT(*) AS cnt,
               array_agg(DISTINCT tenant_id) AS tenants
        FROM samba_market_account
        WHERE is_default = true
        GROUP BY market_type
        ORDER BY market_type
        """
    )
    for r in rows:
        print(f"  {r['market_type']:15s} cnt={r['cnt']:3d} tenants={r['tenants']}")
    print(f"총 마켓 {len(rows)}개")

    print()
    print("=" * 80)
    print("[2] samba_settings.store_* 원본 (백필 대상)")
    print("=" * 80)
    rows2 = await c.fetch(
        """
        SELECT key, tenant_id, jsonb_typeof(value::jsonb) AS val_type
        FROM samba_settings
        WHERE (key LIKE 'store_%' OR key LIKE '%:store_%')
          AND key NOT LIKE '%store_network_ips%'
          AND key NOT LIKE '%store_scores_cache%'
        ORDER BY key
        """
    )
    for r in rows2:
        print(
            f"  key={r['key'][:60]:60s} tenant={r['tenant_id']} val={r['val_type']}"
        )
    print(f"총 {len(rows2)}건")

    print()
    print("=" * 80)
    print("[3] 백필 누락 진단 — store_* 있는데 market_account default 없는 케이스")
    print("=" * 80)
    rows3 = await c.fetch(
        """
        WITH src AS (
            SELECT
                CASE WHEN key LIKE 'store_%' THEN key
                     ELSE substring(key from position(':' in key) + 1)
                END AS store_key,
                COALESCE(tenant_id, split_part(key, ':', 1)) AS tid,
                CASE WHEN key LIKE 'store_%' THEN tenant_id
                     ELSE COALESCE(tenant_id, split_part(key, ':', 1))
                END AS tid_resolved
            FROM samba_settings
            WHERE (key LIKE 'store_%' OR key LIKE '%:store_%')
              AND key NOT LIKE '%store_network_ips%'
              AND key NOT LIKE '%store_scores_cache%'
              AND value IS NOT NULL
              AND value::text NOT IN ('null', '{}', '""')
        ),
        mapped AS (
            SELECT
                substring(store_key from 7) AS market_type,
                tid_resolved
            FROM src
        )
        SELECT m.market_type, m.tid_resolved
        FROM mapped m
        WHERE NOT EXISTS (
            SELECT 1 FROM samba_market_account ma
            WHERE ma.market_type = m.market_type
              AND ma.is_default = true
              AND COALESCE(ma.tenant_id, '') = COALESCE(m.tid_resolved, '')
        )
        """
    )
    if rows3:
        for r in rows3:
            print(f"  ⚠ 누락: market={r['market_type']} tenant={r['tid_resolved']}")
        print(f"총 누락 {len(rows3)}건 — 7c (legacy 폴백 제거) 진행 시 영향")
    else:
        print("  ✓ 누락 없음 — 모든 store_* 가 market_account 로 백필됨")

    print()
    print("=" * 80)
    print("[4] samba_market_account 전체 개수")
    print("=" * 80)
    total = await c.fetchval("SELECT COUNT(*) FROM samba_market_account")
    active = await c.fetchval(
        "SELECT COUNT(*) FROM samba_market_account WHERE is_active = true"
    )
    default = await c.fetchval(
        "SELECT COUNT(*) FROM samba_market_account WHERE is_default = true"
    )
    print(f"  total={total} active={active} default={default}")

    await c.close()


asyncio.run(main())
