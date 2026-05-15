"""마켓 등록용 외부 이미지 URL 사전 검증.

R2 등 외부 인프라 의존 없이 dead URL과 거부 확장자를 사전 제거한다.

목적 (롯데ON 9999 회피):
- 죽은 외부 CDN URL이 origImgFileNm에 들어가면 롯데ON이 다운로드 실패 후
  "URL 형식이 올바르지 않습니다(9999)"로 응답한다.
- .gif는 등록 거부 사유로 의심되는 확장자 (검증 후 확정 필요).

호출 측은 결과 리스트로 product["images"]를 교체한다.
빈 리스트가 되면 등록 자체를 막는 책임은 호출 측에 있다.
"""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

_HEAD_TIMEOUT = 5.0


def _resolve_referer(url: str) -> str:
    """소싱처별 Referer — 핫링크 차단 회피 (cafe24.py:1217 패턴)."""
    parsed = urlparse(url)
    host = parsed.netloc or ""
    table = {
        "msscdn.net": "https://www.musinsa.com/",
        "fashionplus": "https://www.fashionplus.co.kr/",
        "kream": "https://kream.co.kr/",
        "nike.com": "https://www.nike.com/",
        "a-rt.com": "https://www.a-rt.com/",
        "ssgcdn.com": "https://www.ssg.com/",
        "lotteimall.com": "https://www.lotteon.com/",
        "gsshop": "https://www.gsshop.com/",
        "pstatic.net": "https://smartstore.naver.com/",
        "nstationmall.com": "https://www.nstationmall.com/",
        "speedgabia.com": "https://thenature.speedgabia.com/",
    }
    for needle, ref in table.items():
        if needle in host:
            return ref
    return f"{parsed.scheme}://{host}/"


def _is_rejected_extension(url: str) -> bool:
    """롯데ON 거부 확장자 — 가설 검증 단계.

    .gif 등록 실패 사례(9999)에서 발견. 확정 시 분리/문서화.
    """
    lowered = url.lower().split("?", 1)[0]
    return lowered.endswith(".gif")


async def _head_alive(client: httpx.AsyncClient, url: str) -> str | None:
    """단일 URL HEAD 검증 — 거부 확장자/4xx/예외 시 None."""
    if _is_rejected_extension(url):
        logger.info(f"[image_validator] 거부 확장자 제외: {url[:100]}")
        return None

    headers = {
        "Referer": _resolve_referer(url),
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"),
        "Accept": "image/jpeg,image/png,image/webp,image/*;q=0.8",
    }
    try:
        resp = await client.head(url, headers=headers, follow_redirects=True)
        if resp.status_code < 400:
            return url
        # 일부 CDN(예: contents.lotteon.com)은 HEAD를 403/405로 막고 GET만 허용 →
        # Range 1바이트만 받아 살아있는지 확인 (전체 다운로드 회피)
        if resp.status_code in (403, 405, 501):
            try:
                get_headers = {**headers, "Range": "bytes=0-0"}
                get_resp = await client.get(
                    url, headers=get_headers, follow_redirects=True
                )
                if get_resp.status_code < 400:
                    return url
                logger.info(
                    f"[image_validator] dead URL 제외 (HEAD {resp.status_code} / "
                    f"GET {get_resp.status_code}): {url[:100]}"
                )
                return None
            except Exception as ge:
                logger.info(f"[image_validator] GET 폴백 실패 제외: {url[:100]} ({ge})")
                return None
        logger.info(
            f"[image_validator] dead URL 제외 ({resp.status_code}): {url[:100]}"
        )
        return None
    except Exception as e:
        logger.info(f"[image_validator] HEAD 실패 제외: {url[:100]} ({e})")
        return None


async def filter_alive_urls(urls: list[str]) -> list[str]:
    """이미지 URL 리스트 → 살아있고 등록 가능한 URL만 반환.

    필터링:
    1. .gif 등 거부 확장자 제외
    2. HTTP 4xx/5xx 응답 제외
    3. 네트워크 예외 제외 (timeout/DNS/연결거부)

    동시성: 전체 URL을 병렬로 HEAD. DB 등 외부 의존 없음.
    빈 입력은 빈 리스트.
    """
    if not urls:
        return []

    async with httpx.AsyncClient(
        timeout=_HEAD_TIMEOUT, follow_redirects=True
    ) as client:
        results = await asyncio.gather(
            *[_head_alive(client, u) for u in urls],
            return_exceptions=False,
        )
    alive = [u for u in results if u is not None]
    excluded = len(urls) - len(alive)
    if excluded:
        logger.warning(
            f"[image_validator] 입력 {len(urls)}장 → 통과 {len(alive)}장 "
            f"(제외 {excluded}장)"
        )
    return alive
