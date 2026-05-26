"""SambaWave Forbidden word service."""

import re
from typing import Any, Dict, List, Optional

from backend.domain.samba.forbidden.model import SambaForbiddenWord, SambaSettings
from backend.domain.samba.forbidden.repository import (
    SambaForbiddenWordRepository,
    SambaSettingsRepository,
)


class SambaForbiddenService:
    def __init__(
        self,
        word_repo: SambaForbiddenWordRepository,
        settings_repo: SambaSettingsRepository,
    ):
        self.word_repo = word_repo
        self.settings_repo = settings_repo

    # ==================== Forbidden Word CRUD ====================

    async def list_words(
        self, skip: int = 0, limit: int = 0
    ) -> List[SambaForbiddenWord]:
        return await self.word_repo.list_async(
            skip=skip, limit=limit if limit > 0 else None, order_by="-created_at"
        )

    async def get_word(self, word_id: str) -> Optional[SambaForbiddenWord]:
        return await self.word_repo.get_async(word_id)

    async def create_word(self, data: Dict[str, Any]) -> SambaForbiddenWord:
        return await self.word_repo.create_async(**data)

    async def update_word(
        self, word_id: str, data: Dict[str, Any]
    ) -> Optional[SambaForbiddenWord]:
        return await self.word_repo.update_async(word_id, **data)

    async def delete_word(self, word_id: str) -> bool:
        return await self.word_repo.delete_async(word_id)

    async def list_by_type(self, type: str) -> List[SambaForbiddenWord]:
        return await self.word_repo.list_by_type(type)

    async def list_active(self, type: str) -> List[SambaForbiddenWord]:
        return await self.word_repo.list_active(type)

    # ==================== Settings CRUD ====================

    async def get_setting(self, key: str) -> Any:
        """설정값 조회 - .value만 반환."""
        row = await self.settings_repo.find_by_async(key=key)
        return row.value if row else None

    async def save_setting(self, key: str, value: Any) -> SambaSettings:
        """설정값 upsert. PostgreSQL ON CONFLICT 로 race condition 차단.

        과거 find-then-create 패턴은 동시 호출 시 두 트랜잭션이 모두 None 을 받고
        INSERT 를 시도해 samba_settings_pkey UniqueViolationError 발생.
        오토튠 PC 분담 저장처럼 멀티 워커가 동시에 같은 key 를 쓰는 경로에서 빈번.
        """
        from datetime import UTC, datetime
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        now = datetime.now(UTC)
        stmt = (
            pg_insert(SambaSettings)
            .values(key=key, value=value, updated_at=now)
            .on_conflict_do_update(
                index_elements=["key"],
                set_={"value": value, "updated_at": now},
            )
        )
        session = self.settings_repo.session
        await session.execute(stmt)
        await session.commit()
        # 호출자는 반환값을 쓰지 않음 — 메모리 객체만 구성해 시그니처 호환.
        return SambaSettings(key=key, value=value, updated_at=now)

    # ==================== Filtering Logic ====================
    # Ported from js/modules/forbidden.js

    async def filter_products(
        self, products: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Filter out products containing active forbidden words in name.

        Ported from js/modules/forbidden.js filterProducts().
        """
        forbidden_active = await self.word_repo.list_active("forbidden")
        if not forbidden_active:
            return products

        filtered = []
        for product in products:
            is_valid = True
            name = (product.get("name") or "").lower()
            for fw in forbidden_active:
                word = fw.word.lower()
                if fw.scope in ("title", "both") and word in name:
                    is_valid = False
                    break
            if is_valid:
                filtered.append(product)

        return filtered

    async def clean_product_name(self, name: str) -> str:
        """Remove active deletion words from product name.

        Ported from js/modules/forbidden.js cleanProductName().
        """
        if not name:
            return name

        deletion_active = await self.word_repo.list_active("deletion")
        title_words = [w for w in deletion_active if w.scope in ("title", "both")]

        cleaned = name
        for dw in title_words:
            pattern = re.escape(dw.word)
            cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE).strip()

        # Collapse multiple spaces
        return re.sub(r"\s+", " ", cleaned).strip()

    async def validate_product(self, product: Dict[str, Any]) -> Dict[str, Any]:
        """Validate a product against all active forbidden/deletion words.

        Ported from js/modules/forbidden.js validateProduct().

        Returns dict with:
            is_valid: bool - True if no forbidden words found
            forbidden_found: list of forbidden words found in the product name
            deletion_found: list of deletion words found in the product name
            clean_name: str - product name with deletion words removed
        """
        # limit=10000 — 금지어 테이블은 보통 수백~수천개, 만약을 대비한 상한.
        # CLAUDE.md 성능 규칙: filter_by_async는 limit 명시 필수.
        all_active = await self.word_repo.filter_by_async(is_active=True, limit=10000)

        forbidden_found: List[str] = []
        deletion_found: List[str] = []
        raw_name = product.get("name", "")
        name = (raw_name or "").lower()

        for fw in all_active:
            word = fw.word.lower()
            in_title = word in name if name else False

            if in_title and fw.scope in ("title", "both"):
                if fw.type == "forbidden":
                    forbidden_found.append(fw.word)
                else:
                    deletion_found.append(fw.word)

        # 이미 조회한 all_active로 직접 clean 처리 (중복 DB 조회 방지)
        cleaned = raw_name or ""
        deletion_words = [
            w
            for w in all_active
            if w.type == "deletion" and w.scope in ("title", "both")
        ]
        for dw in deletion_words:
            pattern = re.escape(dw.word)
            cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE).strip()
        clean_name = re.sub(r"\s+", " ", cleaned).strip()

        return {
            "is_valid": len(forbidden_found) == 0,
            "forbidden_found": forbidden_found,
            "deletion_found": deletion_found,
            "clean_name": clean_name,
        }
