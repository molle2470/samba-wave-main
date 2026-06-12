"""SambaWave Forbidden word repository."""

from typing import List

from backend.domain.shared.base_repository import BaseRepository
from backend.domain.samba.forbidden.model import SambaForbiddenWord, SambaSettings


class SambaForbiddenWordRepository(BaseRepository[SambaForbiddenWord]):
    def __init__(self, session):
        super().__init__(session, SambaForbiddenWord)

    async def list_by_type(self, type: str) -> List[SambaForbiddenWord]:
        return await self.filter_by_async(
            type=type, order_by="created_at", order_by_desc=True
        )

    async def list_active(self, type: str) -> List[SambaForbiddenWord]:
        return await self.filter_by_async(
            type=type, is_active=True, order_by="created_at", order_by_desc=True
        )

    async def list_active_for_market(
        self, type: str, market: str
    ) -> List[SambaForbiddenWord]:
        """공통(market IS NULL) + 해당 마켓 전용 활성 단어 합산 조회."""
        from sqlalchemy import or_
        from sqlmodel import select

        stmt = (
            select(SambaForbiddenWord)
            .where(
                SambaForbiddenWord.type == type,
                SambaForbiddenWord.is_active == True,  # noqa: E712
                or_(
                    SambaForbiddenWord.market == None,  # noqa: E711
                    SambaForbiddenWord.market == market,
                ),
            )
            .order_by(SambaForbiddenWord.created_at.desc())
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()


class SambaSettingsRepository(BaseRepository[SambaSettings]):
    def __init__(self, session):
        super().__init__(session, SambaSettings)
