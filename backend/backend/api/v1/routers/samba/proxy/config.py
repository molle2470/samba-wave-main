"""proxy 설정/연결/IP 관련 엔드포인트."""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Depends, Form
from pydantic import BaseModel
from sqlmodel.ext.asyncio.session import AsyncSession

from backend.db.orm import get_read_session_dependency, get_write_session_dependency
from backend.domain.user.auth_service import get_user_id

from ._helpers import _get_setting, _set_setting

router = APIRouter(tags=["samba-proxy"])


# ── Cloud Run 외부 IP 확인 ──
@router.get("/myip")
async def get_my_ip() -> dict[str, Any]:
    """Cloud Run 컨테이너의 외부 IP 주소를 반환한다 (IPv4/IPv6)."""
    result: dict[str, Any] = {}
    async with httpx.AsyncClient(timeout=5) as client:
        try:
            resp = await client.get("https://api.ipify.org?format=json")
            result["ipv4"] = resp.json().get("ip", "")
        except Exception:
            result["ipv4"] = ""
        try:
            resp = await client.get("https://api64.ipify.org?format=json")
            result["ipv6"] = resp.json().get("ip", "")
        except Exception:
            result["ipv6"] = ""
    return result


# ── 프록시 설정 관리 API ──


class ProxyConfigItem(BaseModel):
    """프록시 설정 아이템."""

    name: str  # 프록시 이름 (ex: "프록시칩 1")
    url: str = ""  # 프록시 URL (비어있으면 메인 IP)
    purposes: list[str] = []  # transmit | collect | autotune
    enabled: bool = True


class ProxyConfigPayload(BaseModel):
    proxies: list[ProxyConfigItem]


PROXY_SETTINGS_KEY = "proxy_config"


@router.get("/config/proxies")
async def get_proxy_config(
    session: AsyncSession = Depends(get_read_session_dependency),
):
    """프록시 설정 목록 조회."""
    data = await _get_setting(session, PROXY_SETTINGS_KEY)
    return data or []


@router.put("/config/proxies")
async def save_proxy_config(
    payload: ProxyConfigPayload,
    session: AsyncSession = Depends(get_write_session_dependency),
    user_id: str = Depends(get_user_id),
):
    """프록시 설정 저장 (전체 교체) + 오토튠 프록시 캐시 즉시 갱신."""
    items = [p.model_dump() for p in payload.proxies]
    await _set_setting(session, PROXY_SETTINGS_KEY, items)

    # 캐시 무효화 + 새 프록시 목록으로 즉시 갱신 (UI 저장과 실제 적용 사이 지연 제거)
    try:
        from backend.domain.samba.collector.refresher import (
            invalidate_db_proxy_cache,
            refresh_db_proxy_cache,
        )

        invalidate_db_proxy_cache()
        await refresh_db_proxy_cache()
    except Exception:
        pass

    return {"ok": True, "count": len(items)}


@router.post("/config/proxies/test")
async def test_proxy_connection(
    url: str = Form(...),
):
    """프록시 연결 테스트 — httpbin으로 외부 IP 확인."""
    try:
        async with httpx.AsyncClient(
            proxy=url, timeout=httpx.Timeout(10, connect=5)
        ) as client:
            resp = await client.get("https://httpbin.org/ip")
            if resp.status_code == 200:
                origin = resp.json().get("origin", "")
                return {"success": True, "ip": origin}
            return {"success": False, "message": f"HTTP {resp.status_code}"}
    except Exception as e:
        return {"success": False, "message": str(e)[:200]}


@router.get("/musinsa/ip-check")
async def musinsa_ip_check():
    """무신사 CDN 차단 여부 테스트 — 서버 IP 기준."""
    test_url = "https://image.msscdn.net/images/goods_img/20260309/6099644/6099644_17736397410885_500.jpg"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10, connect=5)) as client:
            resp = await client.get(
                test_url,
                headers={
                    "Referer": "https://www.musinsa.com/",
                    "User-Agent": "Mozilla/5.0",
                },
            )
            size = len(resp.content)
            return {
                "status": resp.status_code,
                "size": size,
                "blocked": resp.status_code != 200 or size < 1000,
                "message": "정상"
                if resp.status_code == 200 and size >= 1000
                else f"차단 의심 (HTTP {resp.status_code}, {size}B)",
            }
    except httpx.ConnectTimeout:
        return {"status": 0, "blocked": True, "message": "연결 타임아웃 — IP 차단"}
    except httpx.ReadTimeout:
        return {"status": 0, "blocked": True, "message": "읽기 타임아웃 — IP 차단"}
    except Exception as e:
        return {"status": 0, "blocked": True, "message": f"오류: {type(e).__name__}"}


# ── 확장앱 설정 ──


@router.get("/extension-config")
async def get_extension_config(
    session: AsyncSession = Depends(get_read_session_dependency),
):
    """확장앱에 전달할 최신 설정 (KREAM 셀렉터, 텍스트 패턴 등)."""
    kream_selectors = await _get_setting(session, "kream_selectors")
    return {"selectors": kream_selectors or {}}


# ── 범용 이미지 프록시 ──

from fastapi import HTTPException, Query
from fastapi.responses import Response as FastAPIResponse

# SSRF 방지 — 허용 도메인 목록
_ALLOWED_IMAGE_HOSTS = {
    "musinsa.com",
    "image.musinsa.com",
    "imagescdn.musinsa.com",
    "kream.co.kr",
    "images.kream.co.kr",
    "lotteon.com",
    "gsshop.com",
    "ssg.com",
    "gs.ssgcdn.com",
    "abcmart.com",
    "image.abcmart.com",
    "nike.com",
    "nike-anz.scene7.com",
    "cdn.shopify.com",
}


def _validate_image_url(target: str) -> bool:
    """허용 목록 기반 URL 검증."""
    from urllib.parse import urlparse

    try:
        parsed = urlparse(target)
        host = parsed.netloc.lower()
        # 호스트가 허용 목록에 포함되는지 확인
        for allowed in _ALLOWED_IMAGE_HOSTS:
            if host == allowed or host.endswith("." + allowed):
                return True
        return False
    except Exception:
        return False


@router.get("/image-proxy")
async def image_proxy(
    url: str = Query("", description="이미지 URL"),
) -> FastAPIResponse:
    """외부 이미지 프록시 (핫링크 차단 우회)."""
    if not url:
        raise HTTPException(status_code=400, detail="URL 필요")
    from urllib.parse import unquote

    target = unquote(url)
    # SSRF 방지 — 허용 도메인만 허용
    if not _validate_image_url(target):
        raise HTTPException(status_code=403, detail="허용되지 않는 호스트")
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(
                target, headers={"Referer": target, "User-Agent": "Mozilla/5.0"}
            )
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "image/jpeg")
            return FastAPIResponse(
                content=resp.content,
                media_type=content_type,
                headers={
                    "Cache-Control": "public, max-age=86400",
                    "Access-Control-Allow-Origin": "*",
                },
            )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))
