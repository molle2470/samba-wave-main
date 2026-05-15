"""samba_settings.cloudflare_r2.public_url 을 자체 도메인 proxy 로 변경.

배경:
    롯데홈쇼핑 등 일부 마켓 API가 pub-...r2.dev 도메인을 다운로드 실패 →
    Caddy reverse_proxy(/images/*)를 통한 자체 도메인으로 전환.

전후:
    BEFORE: https://pub-0a884386abd84479a98975550fc32055.r2.dev
    AFTER : https://api.samba-wave.co.kr/images

전제: Caddy /images/* reverse_proxy 가 먼저 배포되어 있어야 한다.
실행 후: bg_worker(로컬 PC) 재시작 필요 — settings 캐시 갱신.
"""

from __future__ import annotations

import asyncio
import json
import logging

import asyncpg

from backend.core.config import settings

logger = logging.getLogger("update_r2_public_url")
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)

NEW_PUBLIC_URL = "https://api.samba-wave.co.kr/images"


async def main():
    conn = await asyncpg.connect(
        host=settings.write_db_host,
        port=settings.write_db_port,
        user=settings.write_db_user,
        password=settings.write_db_password,
        database=settings.write_db_name,
        ssl=False,
    )
    try:
        row = await conn.fetchrow(
            "SELECT value FROM samba_settings WHERE key='cloudflare_r2'"
        )
        if not row:
            logger.error("samba_settings.cloudflare_r2 없음")
            return

        val = row["value"]
        if isinstance(val, str):
            val = json.loads(val)

        logger.info(f"[BEFORE] public_url = {val.get('public_url')}")

        val["public_url"] = NEW_PUBLIC_URL
        new_val_json = json.dumps(val, ensure_ascii=False)

        await conn.execute(
            "UPDATE samba_settings SET value = $1::jsonb WHERE key='cloudflare_r2'",
            new_val_json,
        )

        row2 = await conn.fetchrow(
            "SELECT value FROM samba_settings WHERE key='cloudflare_r2'"
        )
        val2 = row2["value"]
        if isinstance(val2, str):
            val2 = json.loads(val2)
        logger.info(f"[AFTER ] public_url = {val2.get('public_url')}")

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
