# 읽기 전용 — autotune_pc_allowed_sites (device→sites) 실제값 조회.
# 중단 버그 root cause 확정용: 데몬 device 에 site 가 등록돼 있는지 확인.
import asyncio
import json

import asyncpg

from backend.core.config import settings


async def main():
    conn = await asyncpg.connect(
        host="172.18.0.2",
        port=5432,
        user=settings.write_db_user,
        password=settings.write_db_password,
        database=settings.write_db_name,
        ssl=False,
    )
    row = await conn.fetchrow(
        "SELECT value FROM samba_settings WHERE key = 'autotune_pc_allowed_sites'"
    )
    if not row:
        print("autotune_pc_allowed_sites 설정 없음 (DB)")
    else:
        val = row["value"]
        if isinstance(val, str):
            val = json.loads(val)
        print("=== autotune_pc_allowed_sites (device -> sites) ===")
        for dev, sites in (val or {}).items():
            tag = "데몬" if str(dev).startswith("samba-daemon-") else "확장앱"
            print(f"  [{tag}] {dev} -> {sites}")
    await conn.close()


asyncio.run(main())
