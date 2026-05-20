"""ESMPlus(지마켓/옥션) 배송정보 조회 프록시."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends
from sqlmodel.ext.asyncio.session import AsyncSession

from backend.db.orm import get_read_session_dependency
from backend.utils.logger import logger

router = APIRouter(tags=["samba-proxy-esm"])


async def _get_esm_client(
    session: AsyncSession,
    market: str,
    account_id: str | None,
):
    """계정 정보로 ESMPlusClient 생성.

    인증 정보 우선순위 (resolve_esm_credentials):
      account.additional_fields > samba_settings.esm_credentials > env.
    """
    from backend.domain.samba.proxy.esmplus import (
        ESMPlusClient,
        resolve_esm_credentials,
    )
    from sqlalchemy import text

    if market not in ("gmarket", "auction"):
        return None

    if account_id:
        result = await session.exec(
            text(
                "SELECT seller_id, additional_fields FROM samba_market_account "
                "WHERE id = :aid AND market_type = :mtype AND is_active = true"
            ).bindparams(aid=account_id, mtype=market)
        )
    else:
        result = await session.exec(
            text(
                "SELECT seller_id, additional_fields FROM samba_market_account "
                "WHERE market_type = :mtype AND is_active = true LIMIT 1"
            ).bindparams(mtype=market)
        )

    row = result.first()
    if not row:
        return None

    seller_id = row[0] or ""
    if not seller_id:
        return None

    # account.additional_fields 기반 인증 시도 (없으면 env fallback)
    class _AccountStub:
        def __init__(self, additional_fields: dict | None) -> None:
            self.additional_fields = additional_fields or {}

    account_stub = _AccountStub(row[1] if len(row) > 1 else None)
    hosting_id, secret_key = await resolve_esm_credentials(session, account_stub)
    if not hosting_id or not secret_key:
        return None

    return ESMPlusClient(hosting_id, secret_key, seller_id, site=market)


@router.get("/esm/{market}/delivery-info")
async def esm_delivery_info(
    market: str,
    account_id: str | None = None,
    session: AsyncSession = Depends(get_read_session_dependency),
) -> dict[str, Any]:
    """ESMPlus 출고지/반품지/발송정책 목록 조회."""
    if market not in ("gmarket", "auction"):
        return {
            "success": False,
            "places": [],
            "dispatchPolicies": [],
            "message": "지원하지 않는 마켓",
        }

    try:
        client = await _get_esm_client(session, market, account_id)
        if not client:
            return {
                "success": False,
                "places": [],
                "dispatchPolicies": [],
                "message": "계정 정보가 없거나 서버 환경변수(ESMPLUS_HOSTING_ID/ESMPLUS_SECRET_KEY)가 설정되지 않았습니다.",
            }

        places, dispatch_policies = await asyncio.gather(
            client.get_places(),
            client.get_dispatch_policies(),
            return_exceptions=True,
        )

        if isinstance(places, Exception):
            logger.warning(f"[ESMPlus/{market}] 출고지 조회 실패: {places}")
            places = []
        if isinstance(dispatch_policies, Exception):
            logger.warning(
                f"[ESMPlus/{market}] 발송정책 조회 실패: {dispatch_policies}"
            )
            dispatch_policies = []

        return {
            "success": True,
            "places": places,
            "dispatchPolicies": dispatch_policies,
        }
    except Exception as exc:
        logger.error(f"[ESMPlus/{market}] 배송정보 조회 실패: {exc}")
        return {
            "success": False,
            "places": [],
            "dispatchPolicies": [],
            "message": str(exc),
        }
