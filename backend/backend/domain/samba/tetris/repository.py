"""SambaWave Tetris 정책 배치 repository."""

from typing import Optional

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from backend.domain.shared.base_repository import BaseRepository
from backend.domain.samba.account.model import SambaMarketAccount
from backend.domain.samba.tetris.model import SambaTetrisAssignment


class SambaTetrisRepository(BaseRepository[SambaTetrisAssignment]):
    """테트리스 배치 리포지토리."""

    def __init__(self, session: AsyncSession):
        super().__init__(session, SambaTetrisAssignment)

    async def list_by_tenant(
        self,
        tenant_id: Optional[str],
    ) -> list[SambaTetrisAssignment]:
        """테넌트 기준으로 전체 배치 목록 조회 (마켓 계정 + 순서 정렬).

        가드: market_account_id 가 가리키는 SambaMarketAccount 가 실제로
        존재하는 row만 반환. 계정 삭제 후 남은 orphan assignment 는
        제외해 워커가 유령 ID 로 빈 잡을 만드는 사고를 차단한다.
        """
        stmt = (
            select(SambaTetrisAssignment)
            .join(
                SambaMarketAccount,
                SambaMarketAccount.id == SambaTetrisAssignment.market_account_id,
            )
            .where(SambaTetrisAssignment.tenant_id == tenant_id)
            .order_by(
                SambaTetrisAssignment.market_account_id,
                SambaTetrisAssignment.position_order,
            )
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def find_existing(
        self,
        tenant_id: Optional[str],
        source_site: str,
        brand_name: str,
        market_account_id: str,
    ) -> Optional[SambaTetrisAssignment]:
        """동일한 소싱처·브랜드·계정 조합 배치가 이미 존재하는지 확인."""
        return await self.find_by_async(
            tenant_id=tenant_id,
            source_site=source_site,
            brand_name=brand_name,
            market_account_id=market_account_id,
        )
