"""proxy 패키지 공유 헬퍼 함수 — DB 설정 읽기/쓰기 및 클라이언트 팩토리."""

from __future__ import annotations

from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from backend.domain.samba.forbidden.repository import SambaSettingsRepository
from backend.domain.samba.proxy.gsshop import GsShopClient
from backend.domain.samba.proxy.kream import KreamClient
from backend.domain.samba.proxy.lottehome import LotteHomeClient
from backend.domain.samba.proxy.musinsa import MusinsaClient


async def _get_setting(
    session: AsyncSession, key: str, tenant_id: str | None = None
) -> Any:
    """samba_settings 테이블에서 설정값 조회 (암호화 키는 자동 복호화).

    tenant_id 지정 시 '{tenant_id}:{key}' 형식으로 조회 — 멀티테넌트 환경 지원.
    tenant_id 미지정 시 bare 키 우선 → 단일 테넌트 환경에서 '{*}:{key}' 자동 폴백
    (워커/플러그인이 HTTP 컨텍스트 없이 호출하는 경우 대비).
    """
    from sqlalchemy import select
    from backend.utils.crypto import is_encrypted_key, decrypt_value
    from backend.domain.samba.forbidden.model import SambaSettings

    effective_key = f"{tenant_id}:{key}" if tenant_id else key
    repo = SambaSettingsRepository(session)
    row = await repo.find_by_async(key=effective_key)
    # 멀티테넌트 격리(2026-05-18) 이전 저장 데이터는 bare 키로 남아있음 → 폴백
    if row is None and tenant_id:
        row = await repo.find_by_async(key=key)
    # tenant_id 미지정 + bare 미존재 → 테넌트 prefixed 키 자동 폴백
    # 워커/dispatcher/plugins가 HTTP request 없이 호출하는 경우 처리.
    # 다중 매칭 시 가장 먼저 발견된 1개 반환 (단일 테넌트 환경 가정).
    if row is None and not tenant_id:
        stmt = select(SambaSettings).where(SambaSettings.key.like(f"%:{key}")).limit(2)
        result = await session.execute(stmt)
        candidates = result.scalars().all()
        if len(candidates) == 1:
            row = candidates[0]
        elif len(candidates) > 1:
            from backend.utils.logger import logger as _lg

            _lg.warning(
                f"[_get_setting] tenant_id 미지정 + 다중 테넌트 후보 {len(candidates)}개 — "
                f"key={key!r} 첫번째 사용. 호출자 tenant_id 전달 필요."
            )
            row = candidates[0]
    if row:
        val = row.value
        if val and is_encrypted_key(key) and isinstance(val, str):
            val = decrypt_value(val)
        return val
    return None


async def _set_setting(
    session: AsyncSession, key: str, value: Any, tenant_id: str | None = None
) -> None:
    """samba_settings 테이블에 설정값 저장 (암호화 키는 자동 암호화).

    tenant_id 지정 시 '{tenant_id}:{key}' 형식으로 저장 — 멀티테넌트 환경 지원.
    """
    from backend.utils.crypto import is_encrypted_key, encrypt_value
    from backend.domain.samba.forbidden.service import SambaForbiddenService
    from backend.domain.samba.forbidden.repository import SambaForbiddenWordRepository

    effective_key = f"{tenant_id}:{key}" if tenant_id else key
    if value and is_encrypted_key(key) and isinstance(value, str):
        value = encrypt_value(value)

    svc = SambaForbiddenService(
        SambaForbiddenWordRepository(session), SambaSettingsRepository(session)
    )
    await svc.save_setting(effective_key, value)


async def _get_musinsa_client(session: AsyncSession) -> MusinsaClient:
    """무신사 클라이언트 생성 헬퍼."""
    cookie = await _get_setting(session, "musinsa_cookie") or ""
    return MusinsaClient(cookie=str(cookie))


async def _get_kream_client(session: AsyncSession) -> KreamClient:
    """KREAM 클라이언트 생성 헬퍼."""
    token = await _get_setting(session, "kream_token") or ""
    cookie = await _get_setting(session, "kream_cookie") or ""
    return KreamClient(token=str(token), cookie=str(cookie))


async def _get_lotte_client(session: AsyncSession) -> LotteHomeClient:
    """롯데홈쇼핑 클라이언트 생성 헬퍼."""
    creds = await _get_setting(session, "lottehome_credentials") or {}
    if not isinstance(creds, dict):
        creds = {}
    return LotteHomeClient(
        user_id=creds.get("userId", ""),
        password=creds.get("password", ""),
        agnc_no=creds.get("agncNo", ""),
        env=creds.get("env", "prod"),
    )


async def _get_gs_client(session: AsyncSession) -> GsShopClient:
    """GS샵 클라이언트 생성 헬퍼."""
    creds = await _get_setting(session, "gsshop_credentials") or {}
    if not isinstance(creds, dict):
        creds = {}
    return GsShopClient(
        sup_cd=creds.get("supCd", ""),
        aes_key=creds.get("aesKey", ""),
        sub_sup_cd=creds.get("subSupCd", ""),
        env=creds.get("env", "dev"),
    )


async def _get_ss_client(session: AsyncSession):
    """스마트스토어 클라이언트 생성 헬퍼."""
    from backend.domain.samba.proxy.smartstore import SmartStoreClient
    from sqlalchemy import text

    result = await session.exec(
        text(
            "SELECT additional_fields FROM samba_market_account WHERE market_type='smartstore' LIMIT 1"
        )
    )
    row = result.first()
    if not row or not row[0]:
        return None
    extras = row[0] if isinstance(row[0], dict) else {}
    cid = extras.get("clientId", "")
    csec = extras.get("clientSecret", "")
    if not cid or not csec:
        return None
    return SmartStoreClient(cid, csec)
