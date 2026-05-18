"""테넌트 컨텍스트 — 요청 단위 tenant_id를 ORM 레이어에 자동 노출.

HTTP 요청은 TenantContextMiddleware가 JWT의 tid 클레임을 contextvar에 세팅.
ORM SELECT/INSERT는 db/orm.py의 do_orm_execute 이벤트가 자동으로
WHERE tenant_id = ? 필터와 tenant_id 자동 채움을 적용한다.

워커/마이그레이션 등 HTTP 요청이 아닌 컨텍스트는 contextvar=None → 필터 패스.
"""

from contextvars import ContextVar
from typing import Optional


# 현재 요청의 tenant_id (HTTP 요청 시 미들웨어가 세팅)
current_tenant_id: ContextVar[Optional[str]] = ContextVar(
    "current_tenant_id", default=None
)
