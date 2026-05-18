"""SambaWave 모니터링 이벤트 저장소."""

from datetime import datetime
from typing import Any, Dict, List

from sqlalchemy import func
from sqlmodel import select

from backend.domain.shared.base_repository import BaseRepository
from backend.domain.samba.warroom.model import SambaMonitorEvent


class SambaMonitorEventRepository(BaseRepository[SambaMonitorEvent]):
    def __init__(self, session):
        super().__init__(session, SambaMonitorEvent)

    async def list_recent(self, limit: int = 50) -> List[SambaMonitorEvent]:
        """최근 이벤트 조회 (created_at DESC)."""
        stmt = (
            select(SambaMonitorEvent)
            .order_by(SambaMonitorEvent.created_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_by_type_since(
        self,
        since: datetime,
    ) -> Dict[str, int]:
        """특정 시각 이후 event_type별 카운트.

        projection 쿼리이므로 ORM 자동 필터 우회 — contextvar에서 직접 가져와 적용.
        """
        from backend.core.tenant_context import current_tenant_id as _ctv

        _tid = _ctv.get()
        stmt = (
            select(
                SambaMonitorEvent.event_type,
                func.count(SambaMonitorEvent.id),
            )
            .where(SambaMonitorEvent.created_at >= since)
            .group_by(SambaMonitorEvent.event_type)
        )
        if _tid is not None:
            stmt = stmt.where(SambaMonitorEvent.tenant_id == _tid)
        result = await self.session.execute(stmt)
        return {row[0]: row[1] for row in result.all()}

    async def list_by_severity(
        self,
        severity: str,
        limit: int = 20,
    ) -> List[SambaMonitorEvent]:
        """심각도별 최근 이벤트 조회."""
        stmt = (
            select(SambaMonitorEvent)
            .where(SambaMonitorEvent.severity == severity)
            .order_by(SambaMonitorEvent.created_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_type(
        self,
        event_type: str,
        limit: int = 50,
        since: datetime | None = None,
    ) -> List[SambaMonitorEvent]:
        """이벤트 타입별 최근 이벤트 조회. since 지정 시 DB 레벨에서 시간 필터링."""
        stmt = select(SambaMonitorEvent).where(
            SambaMonitorEvent.event_type == event_type
        )
        if since:
            stmt = stmt.where(SambaMonitorEvent.created_at >= since)
        stmt = stmt.order_by(SambaMonitorEvent.created_at.desc()).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_latest_per_site(
        self,
        event_type: str,
        per_site_limit: int = 2,
    ) -> List[SambaMonitorEvent]:
        """이벤트 타입별 source_site당 최신 N건 조회."""
        from sqlalchemy import literal_column

        row_num = (
            func.row_number()
            .over(
                partition_by=SambaMonitorEvent.source_site,
                order_by=SambaMonitorEvent.created_at.desc(),
            )
            .label("rn")
        )

        from datetime import datetime, timedelta, timezone

        cutoff = datetime.now(timezone.utc) - timedelta(days=1)
        subq = (
            select(SambaMonitorEvent.id, row_num).where(
                SambaMonitorEvent.event_type == event_type,
                SambaMonitorEvent.source_site.is_not(None),
                SambaMonitorEvent.created_at >= cutoff,
            )
        ).subquery()

        stmt = (
            select(SambaMonitorEvent)
            .join(subq, SambaMonitorEvent.id == subq.c.id)
            .where(literal_column("rn") <= per_site_limit)
            .order_by(SambaMonitorEvent.created_at.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_changes_per_site(
        self,
        event_types: List[str],
        per_site_limit: int = 5,
    ) -> List[SambaMonitorEvent]:
        """소싱처·이벤트타입별 최신 N건 조회 (price_changed/sold_out 등)."""
        from sqlalchemy import literal_column

        row_num = (
            func.row_number()
            .over(
                partition_by=[
                    SambaMonitorEvent.source_site,
                    SambaMonitorEvent.event_type,
                ],
                order_by=SambaMonitorEvent.created_at.desc(),
            )
            .label("rn")
        )

        from datetime import datetime, timedelta, timezone

        cutoff = datetime.now(timezone.utc) - timedelta(days=1)
        subq = (
            select(SambaMonitorEvent.id, row_num).where(
                SambaMonitorEvent.event_type.in_(event_types),
                SambaMonitorEvent.source_site.is_not(None),
                SambaMonitorEvent.created_at >= cutoff,
            )
        ).subquery()

        stmt = (
            select(SambaMonitorEvent)
            .join(subq, SambaMonitorEvent.id == subq.c.id)
            .where(literal_column("rn") <= per_site_limit)
            .order_by(
                SambaMonitorEvent.source_site,
                SambaMonitorEvent.event_type,
                SambaMonitorEvent.created_at.desc(),
            )
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_recent_changes_for_markets(
        self,
        event_types: List[str],
        limit_per_type: int = 200,
    ) -> List[SambaMonitorEvent]:
        """판매처 fan-out 용 — event_type별 각각 최신 N건씩 조회.

        단순 limit 방식은 가격변동이 폭주하는 시간대에 sold_out/restock이
        윈도우 밖으로 밀려나 마켓 카드에 표시되지 않는 문제가 있어,
        event_type별로 partition_by 윈도우로 균등 추출한다.

        24h 이내로 컷오프 — 시간 필터가 없으면 이벤트 테이블 누적 시
        풀스캔/외부 정렬로 워룸 마운트가 수분 단위로 늦어진다.
        워룸 카드는 최신 N건만 노출하므로 24h 이상은 의미가 없다.
        """
        from sqlalchemy import literal_column
        from datetime import datetime, timedelta, timezone

        cutoff = datetime.now(timezone.utc) - timedelta(days=1)

        row_num = (
            func.row_number()
            .over(
                partition_by=SambaMonitorEvent.event_type,
                order_by=SambaMonitorEvent.created_at.desc(),
            )
            .label("rn")
        )
        subq = (
            select(SambaMonitorEvent.id, row_num).where(
                SambaMonitorEvent.event_type.in_(event_types),
                SambaMonitorEvent.product_id.is_not(None),
                SambaMonitorEvent.created_at >= cutoff,
            )
        ).subquery()

        stmt = (
            select(SambaMonitorEvent)
            .join(subq, SambaMonitorEvent.id == subq.c.id)
            .where(literal_column("rn") <= limit_per_type)
            .order_by(SambaMonitorEvent.created_at.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def cleanup_old(self, before: datetime) -> int:
        """오래된 이벤트 정리."""
        from sqlalchemy import delete as sa_delete

        stmt = sa_delete(SambaMonitorEvent).where(SambaMonitorEvent.created_at < before)
        result = await self.session.execute(stmt)
        return result.rowcount or 0

    async def update_event_detail(
        self,
        event_id: str,
        patch: Dict[str, Any],
    ) -> bool:
        """이벤트 detail JSONB 필드 부분 update (merge).

        마켓 판매중지 결과 등 발행 이후 결정되는 값을 기존 이벤트에 반영할 때 사용.
        존재하지 않는 event_id 또는 빈 patch는 무시된다.
        """
        from sqlalchemy.orm.attributes import flag_modified

        if not event_id or not patch:
            return False

        stmt = select(SambaMonitorEvent).where(SambaMonitorEvent.id == event_id)
        result = await self.session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            return False

        merged = dict(row.detail or {})
        merged.update(patch)
        row.detail = merged
        flag_modified(row, "detail")
        self.session.add(row)
        await self.session.flush()
        return True

    async def count_hourly_since(
        self,
        event_type: str,
        since: datetime,
    ) -> List[Dict[str, Any]]:
        """시간대별 이벤트 카운트 (차트용)."""
        from sqlalchemy import extract

        stmt = (
            select(
                extract("hour", SambaMonitorEvent.created_at).label("hour"),
                func.count(SambaMonitorEvent.id).label("cnt"),
            )
            .where(
                SambaMonitorEvent.event_type == event_type,
                SambaMonitorEvent.created_at >= since,
            )
            .group_by("hour")
            .order_by("hour")
        )
        result = await self.session.execute(stmt)
        return [{"hour": int(row[0]), "count": row[1]} for row in result.all()]
