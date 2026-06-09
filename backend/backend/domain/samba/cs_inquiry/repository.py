"""SambaWave CS 문의 repository."""

from datetime import datetime
from typing import List, Optional

from sqlalchemy import func
from sqlmodel import select

from backend.domain.shared.base_repository import BaseRepository
from backend.domain.samba.cs_inquiry.model import SambaCSInquiry


class SambaCSInquiryRepository(BaseRepository[SambaCSInquiry]):
    def __init__(self, session):
        super().__init__(session, SambaCSInquiry)

    async def list_filtered(
        self,
        skip: int = 0,
        limit: int = 30,
        market: Optional[str] = None,
        inquiry_type: Optional[str] = None,
        exclude_inquiry_type: Optional[str] = None,
        reply_status: Optional[str] = None,
        search: Optional[str] = None,
        sort_field: str = "inquiry_date",
        sort_desc: bool = True,
        start_dt: Optional[datetime] = None,
        end_dt: Optional[datetime] = None,
    ) -> List[SambaCSInquiry]:
        """필터 + 정렬 + 페이지네이션 목록 조회."""
        stmt = select(SambaCSInquiry).where(SambaCSInquiry.is_hidden == False)  # noqa: E712

        if market:
            stmt = stmt.where(SambaCSInquiry.market == market)
        if inquiry_type:
            stmt = stmt.where(SambaCSInquiry.inquiry_type == inquiry_type)
        if exclude_inquiry_type:
            # inquiry_type 컬럼은 nullable=False → != 로 안전하게 제외
            stmt = stmt.where(SambaCSInquiry.inquiry_type != exclude_inquiry_type)
        if reply_status:
            stmt = stmt.where(SambaCSInquiry.reply_status == reply_status)
        if search:
            from backend.core.sql_safe import escape_like

            search_pat = f"%{escape_like(search)}%"
            stmt = stmt.where(
                SambaCSInquiry.product_name.ilike(search_pat, escape="\\")  # type: ignore
                | SambaCSInquiry.content.ilike(search_pat, escape="\\")  # type: ignore
                | SambaCSInquiry.market_order_id.ilike(search_pat, escape="\\")  # type: ignore
            )
        if start_dt:
            stmt = stmt.where(SambaCSInquiry.inquiry_date >= start_dt)
        if end_dt:
            stmt = stmt.where(SambaCSInquiry.inquiry_date <= end_dt)

        # 정렬
        col = getattr(SambaCSInquiry, sort_field, SambaCSInquiry.inquiry_date)
        stmt = stmt.order_by(col.desc() if sort_desc else col.asc())
        stmt = stmt.offset(skip).limit(limit)

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_filtered(
        self,
        market: Optional[str] = None,
        inquiry_type: Optional[str] = None,
        exclude_inquiry_type: Optional[str] = None,
        reply_status: Optional[str] = None,
        search: Optional[str] = None,
        start_dt: Optional[datetime] = None,
        end_dt: Optional[datetime] = None,
    ) -> int:
        """필터 적용된 총 개수."""
        # func.count(컬럼) 형태로 entity를 노출해야 ORM 자동 tenant 필터가 적용된다.
        # select(func.count()).select_from(...) 은 column_descriptions에 entity가
        # 잡히지 않아 tenant 필터가 스킵된다(카운트만 전 테넌트로 새는 사각지대).
        stmt = select(func.count(SambaCSInquiry.id)).where(
            SambaCSInquiry.is_hidden == False  # noqa: E712
        )

        if market:
            stmt = stmt.where(SambaCSInquiry.market == market)
        if inquiry_type:
            stmt = stmt.where(SambaCSInquiry.inquiry_type == inquiry_type)
        if exclude_inquiry_type:
            # inquiry_type 컬럼은 nullable=False → != 로 안전하게 제외
            stmt = stmt.where(SambaCSInquiry.inquiry_type != exclude_inquiry_type)
        if reply_status:
            stmt = stmt.where(SambaCSInquiry.reply_status == reply_status)
        if search:
            from backend.core.sql_safe import escape_like

            search_pat = f"%{escape_like(search)}%"
            stmt = stmt.where(
                SambaCSInquiry.product_name.ilike(search_pat, escape="\\")  # type: ignore
                | SambaCSInquiry.content.ilike(search_pat, escape="\\")  # type: ignore
                | SambaCSInquiry.market_order_id.ilike(search_pat, escape="\\")  # type: ignore
            )
        if start_dt:
            stmt = stmt.where(SambaCSInquiry.inquiry_date >= start_dt)
        if end_dt:
            stmt = stmt.where(SambaCSInquiry.inquiry_date <= end_dt)

        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def find_by_external_id(
        self, market: str, external_id: str
    ) -> Optional[SambaCSInquiry]:
        """마켓 + external_id로 문의 조회."""
        stmt = select(SambaCSInquiry).where(
            SambaCSInquiry.market == market,
            SambaCSInquiry.external_id == external_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def find_pending_since(
        self,
        market: str,
        inquiry_type: str,
        since: datetime,
        account_id: Optional[str] = None,
    ) -> List[SambaCSInquiry]:
        """특정 마켓/타입의 pending 문의 중 since 이후 문의 조회."""
        stmt = select(SambaCSInquiry).where(
            SambaCSInquiry.market == market,
            SambaCSInquiry.inquiry_type == inquiry_type,
            SambaCSInquiry.reply_status == "pending",
            SambaCSInquiry.inquiry_date >= since,
        )
        if account_id:
            stmt = stmt.where(SambaCSInquiry.account_id == account_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def delete_batch(self, ids: List[str]) -> int:
        """여러 문의 일괄 삭제."""
        count = 0
        for _id in ids:
            deleted = await self.delete_async(_id)
            if deleted:
                count += 1
        return count
