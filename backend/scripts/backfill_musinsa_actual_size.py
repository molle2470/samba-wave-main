# -*- coding: utf-8 -*-
"""무신사 실측 사이즈표 백필.

기존 수집된 무신사 상품의 extra_data에 actualSize를 채운다.
- 재개 가능: extra_data에 'actualSizeChecked'=true 마커가 없는 상품만 처리.
  (실측 없는 상품도 마커는 박아 재호출 방지)
- 신발 스킵: 실측 없음(null) → 호출 낭비 차단
- 쿠키/프록시 로테이션: 무신사 IP 차단 회피(메인 IP 보호)
- 레이트리밋(429/403) 서킷브레이커: 연속 차단 시 백오프
- 원자 JSONB merge UPSERT: 기존 extra_data 키 보존 (verify 완료)

실행(컨테이너 내, detached):
  nohup /app/backend/.venv/bin/python3 /tmp/backfill_musinsa_actual_size.py \
    > /tmp/backfill_as.log 2>&1 &
"""

import asyncio
import json
import logging
import os

import asyncpg
import httpx

from backend.core.config import settings
from backend.domain.samba.collector.refresher import (
    _get_musinsa_cookies,
    _fetch_all_db_proxies,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("backfill_as")

BASE = "https://goods-detail.musinsa.com/api2/goods"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Referer": "https://www.musinsa.com/",
    "Origin": "https://www.musinsa.com",
}

BATCH = 300  # 한 번에 조회할 상품 수
CONCURRENCY = 3  # 동시 요청
INTERVAL = 0.5  # 요청 간 최소 간격(초)
RATE_LIMIT_COOLDOWN = 60  # 연속 차단 시 대기(초)
MAX_PRODUCTS = int(os.getenv("BACKFILL_MAX", "0"))  # 0=무제한, >0=테스트 캡

UPSERT_SQL = """
    UPDATE samba_collected_product
    SET extra_data = (
        COALESCE(extra_data::jsonb, '{}'::jsonb)
        || jsonb_build_object('actualSize', $1::jsonb, 'actualSizeChecked', true)
    )::json
    WHERE id = $2
"""

SELECT_SQL = """
    SELECT id, site_product_id
    FROM samba_collected_product
    WHERE source_site = 'MUSINSA'
      AND NOT (extra_data::jsonb ? 'actualSizeChecked')
      AND COALESCE(category, '') NOT ILIKE '%신발%'
      AND COALESCE(category, '') NOT ILIKE '%슈즈%'
      AND COALESCE(category, '') NOT ILIKE '%스니커%'
    LIMIT $1
"""


class Rotator:
    def __init__(self, items: list[str]):
        self.items = items or [""]
        self.i = 0

    def next(self) -> str:
        v = self.items[self.i % len(self.items)]
        self.i += 1
        return v


async def fetch_actual_size(spid: str, cookie: str, proxy: str) -> tuple[str, object]:
    """actual-size 1건 조회.

    반환 (상태, 값):
      ('ok', dict|None)  — 200 정상. dict=실측있음, None=실측없음. 둘 다 마커 박음.
      ('rate', None)     — 429/403 차단. 마커 안 박음(재시도).
      ('err', None)      — 기타 오류. 마커 안 박음(재시도).
    """
    kw: dict = {"timeout": httpx.Timeout(30, connect=10.0)}
    if proxy:
        kw["proxy"] = proxy
    h = dict(HEADERS)
    if cookie:
        h["Cookie"] = cookie
    try:
        async with httpx.AsyncClient(**kw) as c:
            r = await c.get(f"{BASE}/{spid}/actual-size", headers=h)
        if r.status_code in (429, 403):
            return ("rate", None)
        if r.status_code != 200:
            return ("err", None)
        j = r.json()
        if (j.get("meta") or {}).get("result") != "SUCCESS":
            return ("err", None)
        data = j.get("data")
        if not data or not isinstance(data, dict) or not (data.get("sizes") or []):
            return ("ok", None)  # 실측 없음 — 마커만
        return ("ok", data)
    except Exception:
        return ("err", None)


async def main():
    s = settings
    conn = await asyncpg.connect(
        user=s.write_db_user,
        password=s.write_db_password,
        host=s.write_db_host,
        port=getattr(s, "write_db_port", 5432),
        database=s.write_db_name,
        ssl=False,
    )
    cookies = await _get_musinsa_cookies()
    buckets = await _fetch_all_db_proxies()
    proxies = buckets.get("autotune") or buckets.get("collect") or [""]
    log.info(f"쿠키 {len(cookies)}개, 프록시 {len(proxies)}개 로드")
    if not cookies:
        log.error("무신사 쿠키 없음 — 중단")
        await conn.close()
        return

    ck = Rotator(cookies)
    px = Rotator(proxies)
    sem = asyncio.Semaphore(CONCURRENCY)
    db_lock = asyncio.Lock()  # 단일 asyncpg 커넥션 동시 execute 방지

    total_done = 0
    total_with = 0
    consecutive_rate = 0

    while True:
        limit = BATCH
        if MAX_PRODUCTS:
            remaining = MAX_PRODUCTS - total_done
            if remaining <= 0:
                log.info(f"테스트 캡 {MAX_PRODUCTS}건 도달 — 종료")
                break
            limit = min(BATCH, remaining)
        rows = await conn.fetch(SELECT_SQL, limit)
        if not rows:
            log.info("처리할 상품 없음 — 백필 완료")
            break

        async def handle(row):
            nonlocal total_done, total_with, consecutive_rate
            async with sem:
                spid = row["site_product_id"]
                status, data = await fetch_actual_size(spid, ck.next(), px.next())
                await asyncio.sleep(INTERVAL)
            if status == "rate":
                consecutive_rate += 1
                return
            if status == "err":
                return
            consecutive_rate = 0
            # 'ok' — 마커 박음 (data=dict 또는 None)
            async with db_lock:
                await conn.execute(UPSERT_SQL, json.dumps(data), row["id"])
            total_done += 1
            if data:
                total_with += 1

        # 배치 처리
        await asyncio.gather(*(handle(r) for r in rows))

        log.info(
            f"진행 — 마커완료 누적 {total_done:,}건 "
            f"(실측있음 {total_with:,}건), 이번배치 {len(rows)}건"
        )

        # 서킷브레이커 — 연속 차단 누적 시 장기 대기 + 프록시/쿠키 회전 가속
        if consecutive_rate >= CONCURRENCY * 3:
            log.warning(
                f"레이트리밋 연속 {consecutive_rate}회 — {RATE_LIMIT_COOLDOWN}초 대기"
            )
            await asyncio.sleep(RATE_LIMIT_COOLDOWN)
            consecutive_rate = 0

    log.info(f"=== 백필 종료: 마커 {total_done:,}건, 실측보유 {total_with:,}건 ===")
    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
