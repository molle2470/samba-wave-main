"""SQLAlchemy ORM 자동 tenant 필터.

current_tenant_id contextvar가 세팅된 상태(=HTTP 요청)에서
- SELECT: 모든 tenant_id 컬럼 있는 모델에 WHERE tenant_id = ? 자동 추가
- INSERT: tenant_id 미세팅 객체에 자동 채움

contextvar=None인 컨텍스트(워커/마이그레이션/내부 잡)는 패스.
"""

import logging
from typing import Iterable, Type

from sqlalchemy import event
from sqlalchemy.orm import Session, with_loader_criteria
from sqlmodel import SQLModel

from backend.core.tenant_context import current_tenant_id

logger = logging.getLogger(__name__)


_tenant_scoped_cache: list[Type] | None = None


def _tenant_scoped_models() -> Iterable[Type]:
    """tenant_id 컬럼이 있는 매핑 모델 클래스 캐시 반환."""
    global _tenant_scoped_cache
    if _tenant_scoped_cache is not None:
        return _tenant_scoped_cache

    result: list[Type] = []
    for mapper in SQLModel.metadata.tables.values():
        if "tenant_id" not in mapper.columns:
            continue
    # 위 metadata.tables 순회는 Table 객체 — class가 필요해서 registry 순회로 교체
    result = []
    try:
        from sqlalchemy.orm import _mapper_registry as _reg  # 내부 (대비)
    except Exception:
        _reg = None

    # SQLAlchemy 2.0: SQLModel.metadata.tables + registry.mappers
    for mapper in list(SQLModel.registry.mappers):
        cls = mapper.class_
        try:
            cols = cls.__table__.columns
        except Exception:
            continue
        if "tenant_id" in cols:
            result.append(cls)

    _tenant_scoped_cache = result
    logger.info(
        f"[tenant_filter] tenant_id 컬럼 보유 모델 {len(result)}개 자동 필터링 활성"
    )
    return result


def register_tenant_filter_events() -> None:
    """SQLAlchemy event 리스너 등록 — 앱 시작 시 한 번만 호출."""

    @event.listens_for(Session, "do_orm_execute")
    def _apply_tenant_filter(orm_execute_state):
        """SELECT 자동 WHERE tenant_id = ? 추가."""
        if not orm_execute_state.is_select:
            return
        tenant_id = current_tenant_id.get()
        if tenant_id is None:
            return

        # 모든 tenant 스코프 모델에 loader_criteria 옵션 추가
        # with_loader_criteria는 statement에 등장하지 않는 모델은 무시
        for cls in _tenant_scoped_models():
            orm_execute_state.statement = orm_execute_state.statement.options(
                with_loader_criteria(
                    cls,
                    cls.tenant_id == tenant_id,
                    include_aliases=True,
                )
            )

    @event.listens_for(Session, "before_flush")
    def _auto_set_tenant_id(session, flush_context, instances):
        """INSERT/UPDATE 시 tenant_id 미세팅 객체에 자동 채움.

        - 신규 객체(new): tenant_id 가 None이면 contextvar 값 세팅
        - 수정 객체(dirty): tenant_id 변경 시도 차단 안 함 (관리자 의도적 변경 허용)
        """
        tenant_id = current_tenant_id.get()
        if tenant_id is None:
            return

        for obj in session.new:
            if not hasattr(obj, "tenant_id"):
                continue
            if getattr(obj, "tenant_id", None) is None:
                obj.tenant_id = tenant_id

    logger.info("[tenant_filter] ORM 자동 tenant 필터 이벤트 등록 완료")
