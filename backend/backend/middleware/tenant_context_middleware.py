"""TenantContextMiddleware — JWT의 tid 클레임을 contextvar에 세팅.

ORM 자동 필터는 backend/core/tenant_context.py의 current_tenant_id를 읽는다.
미들웨어 자체는 인증을 강제하지 않음 (인증 없는 endpoint는 그대로 통과).
"""

import logging
from typing import Optional

import jwt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from backend.core.config import settings
from backend.core.tenant_context import current_tenant_id

logger = logging.getLogger(__name__)


def _extract_tenant_id_from_request(request: Request) -> Optional[str]:
    """Authorization Bearer JWT에서 tid 클레임 추출. 실패 시 None."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None
    token = auth_header.split(" ", 1)[1]
    try:
        payload = jwt.decode(
            token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
        )
        return payload.get("tid")
    except Exception:
        return None


class TenantContextMiddleware(BaseHTTPMiddleware):
    """모든 HTTP 요청에 대해 contextvar 세팅 → ORM 자동 필터 활성."""

    async def dispatch(self, request: Request, call_next):
        tenant_id = _extract_tenant_id_from_request(request)
        token = current_tenant_id.set(tenant_id)
        try:
            return await call_next(request)
        finally:
            current_tenant_id.reset(token)
