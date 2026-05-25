"""마켓 자격증명 우선순위 해석 — 라우터/디스패처/워커 공통 (2026-05-25).

호출 흐름:
1. form_payload 가 있으면 → 폼 입력값 우선 (신규 등록 인증 테스트)
2. account_id 지정 시 → 그 계정 (tenant 일치 검증)
3. find_default(market_type, tenant_id) — is_default 우선, 없으면 updated_at desc
4. samba_settings.store_<market> 레거시 폴백 (마이그레이션 완료 전 호환)

워커/디스패처에서 호출 시 tenant_id=None 이면 NULL tenant 매칭 → 매핑 없으면 즉시
store_* 레거시 폴백으로 떨어진다 (기존 동작 보존).
"""

from __future__ import annotations

from typing import Any, Optional

from sqlmodel.ext.asyncio.session import AsyncSession

from backend.domain.samba.account.credentials import CRED_BUILDERS
from backend.domain.samba.account.model import SambaMarketAccount
from backend.domain.samba.account.repository import SambaMarketAccountRepository


async def resolve_market_creds(
    session: AsyncSession,
    tenant_id: Optional[str],
    market_type: str,
    store_key: str,
    form_payload: Optional[dict] = None,
    account_id: Optional[str] = None,
) -> dict[str, Any]:
    """범용 자격증명 우선순위 해석. credentials 빌더 키 명세 따른 dict 반환.

    market_type 은 CRED_BUILDERS 키 (소문자). store_key 는 레거시 samba_settings key.
    """
    if form_payload:
        cleaned = {k: v for k, v in form_payload.items() if v}
        if cleaned:
            return cleaned

    # session 미제공(테스트/특수 컨텍스트) 시 DB 조회 스킵 → 바로 레거시 폴백 시도.
    if session is not None:
        try:
            repo = SambaMarketAccountRepository(session)
            account: Optional[SambaMarketAccount] = None
            if account_id:
                account = await repo.get_async(account_id)
                if account and tenant_id is not None and account.tenant_id != tenant_id:
                    account = None
            if account is None:
                account = await repo.find_default(market_type, tenant_id)
            if account is not None:
                builder = CRED_BUILDERS.get(market_type.lower())
                if builder is not None:
                    return builder(account)
        except (AttributeError, Exception) as exc:  # noqa: BLE001
            # session mock / 일시 DB 오류 등 — 레거시 폴백으로 graceful degrade
            import logging

            logging.getLogger(__name__).debug(
                f"[resolver] DB 조회 실패 ({market_type}): {exc}. 레거시 폴백 시도."
            )

    # 레거시 폴백 — _get_setting 은 라우터/워커 양쪽에서 사용 가능.
    # module 자체 import 후 attr 접근(get_setting) → monkeypatch 가능.
    # (함수 내 from-import 는 local binding 되어 module-attr 패치가 안 먹는다.)
    from backend.api.v1.routers.samba.proxy import _helpers as _helpers_mod

    try:
        legacy = await _helpers_mod._get_setting(
            session, store_key, tenant_id=tenant_id
        )
    except TypeError:
        # mock 시그니처 tenant_id 미지원 fallback
        legacy = await _helpers_mod._get_setting(session, store_key)
    if isinstance(legacy, dict):
        return legacy
    return {}
