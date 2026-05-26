"""테넌트 미들웨어 — JWT에서 tenant_id 추출."""

import logging
from typing import Optional

from fastapi import Depends, HTTPException, Request
from sqlmodel.ext.asyncio.session import AsyncSession

from backend.db.orm import get_read_session_dependency

logger = logging.getLogger(__name__)


async def get_current_tenant_id(
    request: Request,
    session: AsyncSession = Depends(get_read_session_dependency),
) -> str:
    """JWT → tenant_id 추출. 인증 필수 API에 사용.

    JWT에 tid 클레임이 있으면 DB 조회 없이 바로 반환 (성능 최적화).
    구 토큰(tid 없음)은 DB 폴백으로 처리.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(401, "인증 토큰이 없습니다")

    token = auth_header.split(" ", 1)[1]
    try:
        from backend.core.config import settings
        import jwt

        payload = jwt.decode(
            token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
        )
        user_id = payload.get("sub", "")
    except Exception:
        raise HTTPException(401, "유효하지 않은 토큰입니다")

    if not user_id:
        raise HTTPException(401, "사용자 정보를 찾을 수 없습니다")

    # 신규 토큰: tid 클레임 직접 사용 (DB 조회 없음)
    tenant_id = payload.get("tid")
    if tenant_id:
        return tenant_id

    # 구 토큰 폴백: DB에서 user의 tenant_id 조회
    from backend.domain.samba.user.model import SambaUser
    from sqlmodel import select

    stmt = select(SambaUser).where(SambaUser.id == user_id)
    result = await session.execute(stmt)
    user = result.scalars().first()
    if not user:
        raise HTTPException(401, "사용자를 찾을 수 없습니다")

    tenant_id = getattr(user, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(403, "테넌트가 설정되지 않았습니다. 관리자에게 문의하세요.")

    return tenant_id


async def get_optional_tenant_id(
    request: Request,
    session: AsyncSession = Depends(get_read_session_dependency),
) -> Optional[str]:
    """테넌트 ID 추출 — SaaS 전환 완료 후 사실상 필수.

    [2026-05-18] NULL 차단 강화: tenant_id 없으면 403 반환.
    backfill 완료로 모든 사용자는 tenant_id를 가져야 하므로 NULL은 오류 상태.
    라우터 시그니처는 Optional[str] 유지(코드 변경 최소화), 실제로는 None 안 들어옴.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(401, "인증 토큰이 없습니다")

    token = auth_header.split(" ", 1)[1]
    try:
        from backend.core.config import settings
        import jwt

        payload = jwt.decode(
            token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
        )
        user_id = payload.get("sub", "")
    except Exception:
        raise HTTPException(401, "유효하지 않은 토큰입니다")

    if not user_id:
        raise HTTPException(401, "사용자 정보를 찾을 수 없습니다")

    # 신규 토큰: tid 클레임 직접 사용
    tenant_id = payload.get("tid")
    if tenant_id:
        return tenant_id

    # 구 토큰 폴백: DB 조회
    from backend.domain.samba.user.model import SambaUser
    from sqlmodel import select

    stmt = select(SambaUser).where(SambaUser.id == user_id)
    result = await session.execute(stmt)
    user = result.scalars().first()
    if not user:
        raise HTTPException(401, "사용자를 찾을 수 없습니다")

    tenant_id = getattr(user, "tenant_id", None)
    if not tenant_id:
        logger.warning(
            f"사용자 {user_id}에 tenant_id 없음 — 가입 직후이거나 backfill 누락"
        )
        raise HTTPException(
            403,
            "테넌트가 설정되지 않았습니다. 다시 로그인하거나 관리자에게 문의하세요.",
        )
    return tenant_id


async def require_admin(
    request: Request,
    session: AsyncSession = Depends(get_read_session_dependency),
) -> str:
    """관리자 권한 검사 — is_admin=True인 사용자만 허용.

    반환값: 인증된 사용자 ID (admin 확인 완료).
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(401, "인증 토큰이 없습니다")

    token = auth_header.split(" ", 1)[1]
    try:
        from backend.core.config import settings
        import jwt

        payload = jwt.decode(
            token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
        )
        user_id = payload.get("sub", "")
    except Exception:
        raise HTTPException(401, "유효하지 않은 토큰입니다")

    if not user_id:
        raise HTTPException(401, "사용자 정보를 찾을 수 없습니다")

    from backend.domain.samba.user.model import SambaUser
    from sqlmodel import select

    stmt = select(SambaUser).where(SambaUser.id == user_id)
    result = await session.execute(stmt)
    user = result.scalars().first()
    if not user:
        raise HTTPException(401, "사용자를 찾을 수 없습니다")

    if not user.is_admin:
        raise HTTPException(403, "관리자 권한이 필요합니다")

    return user_id
