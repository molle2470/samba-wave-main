"""SambaWave Category repository."""

from typing import List, Optional

from sqlalchemy import text

from backend.domain.shared.base_repository import BaseRepository
from backend.domain.samba.category.model import SambaCategoryMapping, SambaCategoryTree


class SambaCategoryMappingRepository(BaseRepository[SambaCategoryMapping]):
    def __init__(self, session):
        super().__init__(session, SambaCategoryMapping)

    async def find_mapping(
        self, source_site: str, source_category: str
    ) -> Optional[SambaCategoryMapping]:
        # ORM 대신 raw SQL 사용 — tenant_filter do_orm_execute 이벤트가 자동으로
        # WHERE tenant_id = X 를 추가해 tenant_id=NULL 레거시 row를 놓치기 때문.
        # samba_category_mapping은 (source_site, source_category) UNIQUE이므로
        # 테넌트 필터 없이 조회해도 정확히 1개 row만 반환됨.
        raw = await self.session.execute(
            text(
                "SELECT id, tenant_id, source_site, source_category, "
                "target_mappings, applied_policy_id, created_at, updated_at "
                "FROM samba_category_mapping "
                "WHERE source_site = :ss AND source_category = :sc LIMIT 1"
            ),
            {"ss": source_site, "sc": source_category},
        )
        row = raw.fetchone()
        if row is None:
            return None
        return SambaCategoryMapping(
            id=row[0],
            tenant_id=row[1],
            source_site=row[2],
            source_category=row[3],
            target_mappings=row[4],
            applied_policy_id=row[5],
            created_at=row[6],
            updated_at=row[7],
        )

    async def list_all(self) -> List[SambaCategoryMapping]:
        """전체 매핑 목록 조회 (벌크 매핑용).

        ORM 대신 raw SQL — tenant_id=NULL 레거시 row 포함 조회.
        """
        raw = await self.session.execute(
            text(
                "SELECT id, tenant_id, source_site, source_category, "
                "target_mappings, applied_policy_id, created_at, updated_at "
                "FROM samba_category_mapping "
                "ORDER BY source_site, source_category"
            )
        )
        rows = raw.fetchall()
        return [
            SambaCategoryMapping(
                id=r[0],
                tenant_id=r[1],
                source_site=r[2],
                source_category=r[3],
                target_mappings=r[4],
                applied_policy_id=r[5],
                created_at=r[6],
                updated_at=r[7],
            )
            for r in rows
        ]


class SambaCategoryTreeRepository(BaseRepository[SambaCategoryTree]):
    def __init__(self, session):
        super().__init__(session, SambaCategoryTree)

    async def get_by_site(self, site_name: str) -> Optional[SambaCategoryTree]:
        """Get category tree by site_name (primary key)."""
        return await self.find_by_async(site_name=site_name)

    async def delete_by_site(self, site_name: str) -> bool:
        """Delete category tree by site_name."""
        entity = await self.get_by_site(site_name)
        if not entity:
            return False
        await self.session.delete(entity)
        await self.session.commit()
        return True
