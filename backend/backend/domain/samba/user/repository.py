"""삼바웨이브 사용자 리포지토리."""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import and_
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from backend.domain.shared.base_repository import BaseRepository
from backend.domain.samba.user.model import SambaUser


class SambaUserRepository(BaseRepository[SambaUser]):
    """SambaUser CRUD 리포지토리."""

    def __init__(self, session: AsyncSession):
        super().__init__(session, SambaUser)

    async def find_by_email(self, email: str) -> Optional[SambaUser]:
        """이메일로 사용자 조회 (삭제된 사용자 제외)."""
        stmt = select(SambaUser).where(
            and_(
                SambaUser.email == email,
                SambaUser.deleted_at.is_(None),
            )
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def find_by_email_any(self, email: str) -> Optional[SambaUser]:
        """이메일로 사용자 조회 (삭제된 사용자 포함) — 재가입 복구용."""
        stmt = select(SambaUser).where(SambaUser.email == email)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def soft_delete(self, user_id: str) -> bool:
        """소프트 삭제 (deleted_at 설정)."""
        user = await self.get_async(user_id)
        if not user or user.deleted_at is not None:
            return False

        user.deleted_at = datetime.now(tz=timezone.utc)
        user.updated_at = datetime.now(tz=timezone.utc)
        self.session.add(user)
        await self.session.commit()
        return True
