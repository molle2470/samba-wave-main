"""확장앱 테넌트별 API 키 발급/조회/revoke."""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlmodel.ext.asyncio.session import AsyncSession

from backend.core.config import settings
from backend.db.orm import get_write_session_dependency
from backend.domain.samba.extension_key.model import SambaExtensionKey

router = APIRouter(prefix="/extension-keys", tags=["extension-keys"])
_security = HTTPBearer(auto_error=False)
_UTC = timezone.utc


@dataclass
class _UserCtx:
    user_id: str
    tenant_id: Optional[str]


async def _get_user_ctx(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_security),
) -> _UserCtx:
    if settings.mock_auth_enabled:
        return _UserCtx(user_id="mock-user-001", tenant_id=None)
    if not credentials:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing token")
    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
        if payload.get("type") != "access":
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token type")
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")
        return _UserCtx(user_id=user_id, tenant_id=payload.get("tid"))
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")


def _hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _new_ulid() -> str:
    from ulid import ULID

    return str(ULID())


class _KeyIssueRequest(BaseModel):
    label: Optional[str] = None


class _KeyResponse(BaseModel):
    id: str
    label: Optional[str]
    created_at: datetime
    last_used_at: Optional[datetime]
    revoked_at: Optional[datetime]


class _KeyIssueResponse(_KeyResponse):
    key: str  # 평문 키 — 발급 시 1회만 노출


@router.post("", status_code=201, response_model=_KeyIssueResponse)
async def issue_key(
    body: _KeyIssueRequest,
    request: Request,
    ctx: _UserCtx = Depends(_get_user_ctx),
    session: AsyncSession = Depends(get_write_session_dependency),
):
    """테넌트별 확장앱 키 발급. 평문 키는 이 응답에서만 노출.

    X-Device-Id 헤더가 있으면 device_id 컬럼에 저장(2026-05-20) — 오토튠
    status API의 본인 device 매칭에 사용.
    """
    raw = secrets.token_hex(32)
    _device_id = (request.headers.get("X-Device-Id") or "").strip() or None
    record = SambaExtensionKey(
        id=_new_ulid(),
        key_hash=_hash_key(raw),
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        label=body.label,
        created_at=datetime.now(_UTC),
        device_id=_device_id,
    )
    session.add(record)
    await session.commit()
    return _KeyIssueResponse(
        id=record.id,
        label=record.label,
        created_at=record.created_at,
        last_used_at=None,
        revoked_at=None,
        key=raw,
    )


@router.get("", response_model=list[_KeyResponse])
async def list_keys(
    ctx: _UserCtx = Depends(_get_user_ctx),
    session: AsyncSession = Depends(get_write_session_dependency),
):
    """본인이 발급한 키 목록."""
    stmt = (
        select(SambaExtensionKey)
        .where(SambaExtensionKey.user_id == ctx.user_id)
        .order_by(SambaExtensionKey.created_at.desc())
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [
        _KeyResponse(
            id=r.id,
            label=r.label,
            created_at=r.created_at,
            last_used_at=r.last_used_at,
            revoked_at=r.revoked_at,
        )
        for r in rows
    ]


@router.delete("/{key_id}", status_code=204)
async def revoke_key(
    key_id: str,
    ctx: _UserCtx = Depends(_get_user_ctx),
    session: AsyncSession = Depends(get_write_session_dependency),
):
    """키 revoke — 이후 해당 키로 API 접근 불가."""
    result = await session.execute(
        update(SambaExtensionKey)
        .where(
            SambaExtensionKey.id == key_id,
            SambaExtensionKey.user_id == ctx.user_id,
            SambaExtensionKey.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(_UTC))
    )
    await session.commit()
    if result.rowcount == 0:
        raise HTTPException(404, "키를 찾을 수 없거나 이미 revoke됨")
