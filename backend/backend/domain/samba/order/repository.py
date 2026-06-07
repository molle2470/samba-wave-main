"""SambaWave Order repository."""

from datetime import datetime
from typing import List, Optional

from sqlalchemy import func, or_
from sqlmodel import select

from backend.domain.shared.base_repository import BaseRepository
from backend.domain.samba.order.model import SambaOrder


class SambaOrderRepository(BaseRepository[SambaOrder]):
    def __init__(self, session):
        super().__init__(session, SambaOrder)

    async def list_for_analytics(
        self,
        *,
        tenant_id: Optional[str] = None,
        created_from: Optional[datetime] = None,
        created_to: Optional[datetime] = None,
        paid_from: Optional[datetime] = None,
    ) -> List[SambaOrder]:
        """분석용 주문 조회 — tenant/날짜 필터를 SQL WHERE 로 푸시다운.

        기존 analytics 서비스가 list_async() 로 전체 테이블을 메모리에 적재한 뒤
        파이썬에서 거르던 것을 SQL 레벨로 옮겨 10만+ 주문 환경의 풀로드를 제거한다.
        tenant_id 지정 시 NULL(레거시 backfill 전) row 도 포함 — 기존
        _filter_by_tenant 와 동일한 의미. 날짜 컬럼은 timestamptz 라 tz-aware
        비교가 그대로 instant 비교가 된다(기존 파이썬 필터와 결과 동일).
        """
        conds = []
        if tenant_id:
            conds.append(
                or_(SambaOrder.tenant_id.is_(None), SambaOrder.tenant_id == tenant_id)
            )
        if created_from is not None:
            conds.append(SambaOrder.created_at >= created_from)
        if created_to is not None:
            conds.append(SambaOrder.created_at <= created_to)
        if paid_from is not None:
            conds.append(SambaOrder.paid_at >= paid_from)

        stmt = select(SambaOrder)
        if conds:
            stmt = stmt.where(*conds)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def search(self, query: str) -> List[SambaOrder]:
        from backend.core.sql_safe import escape_like

        lower_q = f"%{escape_like(query.lower())}%"
        stmt = (
            select(SambaOrder)
            .where(
                or_(
                    SambaOrder.order_number.ilike(lower_q, escape="\\"),
                    SambaOrder.customer_name.ilike(lower_q, escape="\\"),
                    SambaOrder.customer_phone.ilike(lower_q, escape="\\"),
                    SambaOrder.product_name.ilike(lower_q, escape="\\"),
                )
            )
            .order_by(SambaOrder.created_at.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_status(self, status: str) -> List[SambaOrder]:
        return await self.filter_by_async(
            status=status, order_by="created_at", order_by_desc=True
        )

    async def list_by_channel(self, channel_id: str) -> List[SambaOrder]:
        return await self.filter_by_async(
            channel_id=channel_id, order_by="created_at", order_by_desc=True
        )

    async def list_by_date_range(
        self, start_date: str, end_date: str
    ) -> List[SambaOrder]:
        # 고객결제일(paid_at) 기준, 없으면 created_at fallback
        date_col = func.coalesce(SambaOrder.paid_at, SambaOrder.created_at)
        stmt = (
            select(SambaOrder)
            .where(
                date_col >= start_date,
                date_col <= end_date,
            )
            .order_by(date_col.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
