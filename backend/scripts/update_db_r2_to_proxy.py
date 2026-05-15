"""DB의 samba_collected_product.images / detail_images / detail_html 에서
pub-...r2.dev URL을 api.samba-wave.co.kr/images proxy URL로 일괄 치환.

배경:
    롯데홈쇼핑 등 일부 마켓 API가 r2.dev 도메인 이미지 다운로드 실패 →
    Caddy reverse_proxy 경유(/images/*)로 자체 도메인 사용.

전제: Caddy /images/* reverse_proxy 가 먼저 배포되어 있어야 한다.
      순서를 어기면 새 URL이 404가 나 이미지가 깨진다.

검증 모드(--dry-run) 우선 실행 권장.
"""

from __future__ import annotations

import argparse
import asyncio
import logging

import asyncpg

from backend.core.config import settings

logger = logging.getLogger("update_db_r2_to_proxy")
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)

OLD_PREFIX = "https://pub-0a884386abd84479a98975550fc32055.r2.dev"
NEW_PREFIX = "https://api.samba-wave.co.kr/images"


def _make_update(col: str) -> str:
    return f"""
UPDATE samba_collected_product
SET {col} = replace({col}::text, '{OLD_PREFIX}', '{NEW_PREFIX}')::jsonb
WHERE {col}::text LIKE '%{OLD_PREFIX}%'
"""


SQL_UPDATE_IMAGES = _make_update("images")
SQL_UPDATE_DETAIL_IMAGES = _make_update("detail_images")

SQL_UPDATE_DETAIL_HTML = f"""
UPDATE samba_collected_product
SET detail_html = replace(detail_html, '{OLD_PREFIX}', '{NEW_PREFIX}')
WHERE detail_html LIKE '%{OLD_PREFIX}%'
"""

SQL_COUNT_BEFORE = f"""
SELECT
  (SELECT COUNT(*) FROM samba_collected_product WHERE images::text LIKE '%{OLD_PREFIX}%') AS images_cnt,
  (SELECT COUNT(*) FROM samba_collected_product WHERE detail_images IS NOT NULL AND detail_images::text LIKE '%{OLD_PREFIX}%') AS detail_images_cnt,
  (SELECT COUNT(*) FROM samba_collected_product WHERE detail_html IS NOT NULL AND detail_html LIKE '%{OLD_PREFIX}%') AS detail_html_cnt
"""


async def main(dry_run: bool):
    conn = await asyncpg.connect(
        host=settings.write_db_host,
        port=settings.write_db_port,
        user=settings.write_db_user,
        password=settings.write_db_password,
        database=settings.write_db_name,
        ssl=False,
    )
    try:
        row = await conn.fetchrow(SQL_COUNT_BEFORE)
        logger.info(
            f"[BEFORE] images={row['images_cnt']:,} "
            f"detail_images={row['detail_images_cnt']:,} "
            f"detail_html={row['detail_html_cnt']:,}"
        )

        if dry_run:
            logger.info("dry-run 모드 — 변경 없이 종료")
            return

        async with conn.transaction():
            r1 = await conn.execute(SQL_UPDATE_IMAGES)
            logger.info(f"images update: {r1}")
            r2 = await conn.execute(SQL_UPDATE_DETAIL_IMAGES)
            logger.info(f"detail_images update: {r2}")
            r3 = await conn.execute(SQL_UPDATE_DETAIL_HTML)
            logger.info(f"detail_html update: {r3}")

        row = await conn.fetchrow(SQL_COUNT_BEFORE)
        logger.info(
            f"[AFTER ] images={row['images_cnt']:,} "
            f"detail_images={row['detail_images_cnt']:,} "
            f"detail_html={row['detail_html_cnt']:,}"
        )

    finally:
        await conn.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument(
        "--apply", action="store_true", help="실제 update 실행 (기본은 dry-run)"
    )
    args = p.parse_args()
    asyncio.run(main(dry_run=not args.apply))
