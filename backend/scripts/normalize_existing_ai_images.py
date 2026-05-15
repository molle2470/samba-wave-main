"""기존 R2 에 저장된 transformed/ai_*.jpg 이미지를 마켓 호환 사양으로 일괄 재처리.

처리 내용:
- 1000x1000 미만 → 비율유지 upscale
- 정사각형 아닌 경우 → 흰배경 padding
- 동일 키로 R2 덮어쓰기 (DB URL 변경 불필요)

배경:
- 롯데홈쇼핑 등 일부 마켓이 대표/추가이미지 최소 해상도 미달 시 placeholder 로
  대체하는 문제 해결 (응답은 성공이지만 이미지만 빠짐).

실행:
    cd backend
    .venv/bin/python scripts/normalize_existing_ai_images.py --dry-run
    .venv/bin/python scripts/normalize_existing_ai_images.py --apply [--limit 100]

R2 액세스 정보는 samba_settings.cloudflare_r2 에서 읽음.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import logging
import re
import sys
from typing import Any

import asyncpg
import boto3
from PIL import Image

from backend.core.config import settings

logger = logging.getLogger("normalize_ai_images")
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)


TARGET = 1000
KEY_RE = re.compile(r"/(transformed/ai_[A-Za-z0-9_]+\.jpg)", re.IGNORECASE)


def _normalize(image_bytes: bytes) -> tuple[bytes, tuple[int, int], tuple[int, int]]:
    """이미지 정규화. 반환: (새 bytes, 원본 (W,H), 결과 (W,H))."""
    src = Image.open(io.BytesIO(image_bytes))
    orig_size = src.size
    if src.mode != "RGB":
        if src.mode in ("RGBA", "LA"):
            bg = Image.new("RGB", src.size, (255, 255, 255))
            rgba = src.convert("RGBA")
            bg.paste(rgba, mask=rgba.split()[3])
            src = bg
        else:
            src = src.convert("RGB")
    W, H = src.size
    if max(W, H) < TARGET:
        scale = TARGET / max(W, H)
        src = src.resize((round(W * scale), round(H * scale)), Image.LANCZOS)
        W, H = src.size
    if W != H:
        side = max(W, H)
        canvas = Image.new("RGB", (side, side), (255, 255, 255))
        canvas.paste(src, ((side - W) // 2, (side - H) // 2))
        src = canvas
    out = io.BytesIO()
    src.save(out, format="JPEG", quality=92, optimize=True)
    return out.getvalue(), orig_size, src.size


async def _collect_keys(conn: asyncpg.Connection) -> list[str]:
    """DB 의 images / detail_images / detail_html 에서 transformed/ai_*.jpg 키 수집."""
    rows = await conn.fetch(
        """
        SELECT images::text AS s FROM samba_collected_product
        WHERE images::text LIKE '%transformed/ai_%'
        UNION ALL
        SELECT detail_images::text FROM samba_collected_product
        WHERE detail_images::text LIKE '%transformed/ai_%'
        UNION ALL
        SELECT detail_html FROM samba_collected_product
        WHERE detail_html LIKE '%transformed/ai_%'
        """
    )
    keys: set[str] = set()
    for r in rows:
        s = r["s"] or ""
        for m in KEY_RE.finditer(s):
            keys.add(m.group(1))
    return sorted(keys)


async def main(dry_run: bool, limit: int | None) -> None:
    # R2 자격증명 로드
    conn = await asyncpg.connect(
        host=settings.write_db_host,
        port=settings.write_db_port,
        user=settings.write_db_user,
        password=settings.write_db_password,
        database=settings.write_db_name,
        ssl=False,
    )
    row = await conn.fetchrow(
        "SELECT value FROM samba_settings WHERE key='cloudflare_r2'"
    )
    val: dict[str, Any] = row["value"]
    if isinstance(val, str):
        val = json.loads(val)

    s3 = boto3.client(
        "s3",
        endpoint_url=f"https://{val['accountId']}.r2.cloudflarestorage.com",
        aws_access_key_id=val["accessKey"],
        aws_secret_access_key=val["secretKey"],
        region_name="auto",
    )
    bucket = val["bucketName"]

    keys = await _collect_keys(conn)
    await conn.close()

    if limit:
        keys = keys[:limit]
    logger.info(f"대상 키 {len(keys):,}건 (dry_run={dry_run})")

    stats = {"skip_big": 0, "resized": 0, "padded": 0, "miss": 0, "err": 0, "ok": 0}

    for i, key in enumerate(keys, 1):
        try:
            # HeadObject 로 메타데이터만 먼저 받아서 크기 확인 — 큰 객체 다운로드 회피
            head = s3.head_object(Bucket=bucket, Key=key)
            content_length = head.get("ContentLength", 0)
            obj = s3.get_object(Bucket=bucket, Key=key)
            data = obj["Body"].read()
            # 1차 빠른 스킵: PIL 로 사이즈만 확인 (재인코딩 X)
            img_meta = Image.open(io.BytesIO(data))
            W0, H0 = img_meta.size
            if W0 >= TARGET and H0 >= TARGET and W0 == H0:
                stats["skip_big"] += 1
                if i % 200 == 0 or i <= 3:
                    logger.info(
                        f"[{i}/{len(keys)}] SKIP {key} {(W0, H0)} (이미 정사각형 ≥ {TARGET})"
                    )
                continue
            new_data, orig, new_size = _normalize(data)
            _ = content_length
            if orig[0] < TARGET or orig[1] < TARGET:
                stats["resized"] += 1
            elif orig[0] != orig[1]:
                stats["padded"] += 1
            else:
                stats["resized"] += 1
            logger.info(
                f"[{i}/{len(keys)}] {key} {orig} → {new_size} "
                f"({len(data):,}→{len(new_data):,}B)" + ("" if dry_run else " UPLOAD")
            )
            if not dry_run:
                s3.put_object(
                    Bucket=bucket,
                    Key=key,
                    Body=new_data,
                    ContentType="image/jpeg",
                    ContentDisposition=f'inline; filename="{key.split("/")[-1]}"',
                    CacheControl="public, max-age=31536000",
                )
                stats["ok"] += 1
        except s3.exceptions.NoSuchKey:
            stats["miss"] += 1
            logger.warning(f"[{i}/{len(keys)}] MISS {key}")
        except Exception as e:
            stats["err"] += 1
            logger.error(f"[{i}/{len(keys)}] ERR {key}: {e}")

    logger.info(f"완료: {stats}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true")
    p.add_argument("--limit", type=int, default=None)
    args = p.parse_args()
    try:
        asyncio.run(main(dry_run=not args.apply, limit=args.limit))
    except KeyboardInterrupt:
        sys.exit(1)
