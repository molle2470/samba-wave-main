"""테넌트 관리 API — 관리자 전용."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel.ext.asyncio.session import AsyncSession

from backend.db.orm import get_read_session_dependency, get_write_session_dependency
from backend.domain.samba.tenant.middleware import require_admin
from backend.domain.samba.tenant.repository import SambaTenantRepository
from backend.domain.samba.tenant.service import SambaTenantService

router = APIRouter(prefix="/tenants", tags=["samba-tenants"])


class TenantCreate(BaseModel):
    name: str
    owner_user_id: str = ""


class TenantUpdate(BaseModel):
    name: Optional[str] = None
    is_active: Optional[bool] = None


@router.get("")
async def list_tenants(
    skip: int = 0,
    limit: int = 50,
    session: AsyncSession = Depends(get_read_session_dependency),
    _admin_id: str = Depends(require_admin),
):
    svc = SambaTenantService(SambaTenantRepository(session))
    return await svc.list_tenants(skip=skip, limit=limit)


@router.post("", status_code=201)
async def create_tenant(
    body: TenantCreate,
    session: AsyncSession = Depends(get_write_session_dependency),
    _admin_id: str = Depends(require_admin),
):
    svc = SambaTenantService(SambaTenantRepository(session))
    tenant = await svc.create_tenant(body.model_dump())
    await session.commit()
    return tenant


@router.get("/{tenant_id}")
async def get_tenant(
    tenant_id: str,
    session: AsyncSession = Depends(get_read_session_dependency),
    _admin_id: str = Depends(require_admin),
):
    svc = SambaTenantService(SambaTenantRepository(session))
    tenant = await svc.get_tenant(tenant_id)
    if not tenant:
        raise HTTPException(404, "테넌트를 찾을 수 없습니다")
    return tenant


@router.put("/{tenant_id}")
async def update_tenant(
    tenant_id: str,
    body: TenantUpdate,
    session: AsyncSession = Depends(get_write_session_dependency),
    _admin_id: str = Depends(require_admin),
):
    svc = SambaTenantService(SambaTenantRepository(session))
    data = body.model_dump(exclude_unset=True)
    result = await svc.update_tenant(tenant_id, data)
    await session.commit()
    return result
