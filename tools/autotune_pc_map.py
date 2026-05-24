"""실제 PC 분담 등록 맵 조회 (samba_settings.autotune_pc_allowed_sites, 읽기 전용)."""

import asyncio
import json

from sqlalchemy import text

from backend.db.orm import get_read_session


async def main():
    async with get_read_session() as s:
        rows = (
            await s.execute(
                text(
                    """
                    SELECT key, value
                    FROM samba_settings
                    WHERE key LIKE '%autotune_pc_allowed_sites'
                    """
                )
            )
        ).fetchall()
        print("=" * 80)
        print("[실제 PC 분담 등록 맵] samba_settings.autotune_pc_allowed_sites")
        print("=" * 80)
        if not rows:
            print("  (등록값 없음)")
        for key, value in rows:
            print(f"\nkey={key}")
            data = value
            if isinstance(value, str):
                try:
                    data = json.loads(value)
                except Exception:
                    pass
            if isinstance(data, dict):
                for dev, sites in data.items():
                    print(f"  {dev:<34} → {sites}")
            else:
                print(f"  raw={data}")


asyncio.run(main())
