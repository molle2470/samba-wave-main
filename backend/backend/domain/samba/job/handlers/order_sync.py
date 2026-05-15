"""order_sync 잡 핸들러 — 활성 마켓 계정 순회 주문 동기화.

원래 `POST /samba/orders/sync-from-markets` 가 단일 요청에서 모든 활성 계정을
순차 처리하던 구조를, 백그라운드 잡으로 분리한 구현.

Caddy `response_header_timeout 120s` 우회 + 진행률 폴링 + 취소 가능.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from backend.domain.samba.job.model import SambaJob
from backend.domain.samba.job.repository import SambaJobRepository
from backend.domain.samba.job.worker import _add_job_log

logger = logging.getLogger(__name__)


def _per_account_timeout_seconds(days: int) -> int:
    # 120~300초 (1차 단축 60~180초는 너무 짧아 정상 응답도 timeout, 이전 180~900초는 너무 김).
    # 롯데ON 한 계정 정상 처리에 주문조회+정산+교환/취소/반품+배송진행 등 5~7개 API 호출이
    # 누적돼 60~90초까지 걸림. hang 좀비는 명시적 rollback + 클라이언트 aclose 로 차단되므로
    # timeout 을 적당히 늘려도 도미노는 안 남.
    return max(120, min(300, days * 60))


async def run(
    job: SambaJob,
    repo: SambaJobRepository,
    session: AsyncSession,
    worker: Any | None = None,
) -> None:
    """활성 마켓 계정을 순회하며 라우터 함수를 직접 호출해 주문 동기화.

    payload:
        days: int = 7        — 동기화 대상 기간(일)
        account_ids: list[str] | None — 특정 계정만 처리 (미지정 시 활성 전체)

    동작:
        1) 활성 계정 목록 조회 (tenant_id 격리)
        2) 진행률 초기화 (total = 계정 수)
        3) 각 계정에 대해 sync_orders_from_markets(account_id=acc.id) 직접 호출
           — 라우터 함수의 1,461줄 로직(스마트스토어/쿠팡/eBay/롯데ON 등)을 그대로 재사용
        4) 매 계정 후 progress 갱신 + 취소 체크
        5) complete_job(result={total_synced, results})
    """
    payload = job.payload or {}
    days = int(payload.get("days") or 7)
    account_ids: list[str] | None = payload.get("account_ids") or None

    # 1) 활성 마켓 계정 조회 — 라우터의 1864-1891 로직과 동일한 정책
    from backend.domain.samba.account.repository import SambaMarketAccountRepository

    acc_repo = SambaMarketAccountRepository(session)
    accs = await acc_repo.filter_by_async(
        is_active=True, order_by="created_at", order_by_desc=True
    )
    # 테넌트 격리: 잡의 tenant_id 가 있으면 해당 테넌트 계정 + 공용(None) 만 유지
    if job.tenant_id is not None:
        accs = [a for a in accs if a.tenant_id == job.tenant_id or a.tenant_id is None]
    # 특정 계정만 지정한 경우 추가 필터
    if account_ids:
        _id_set = set(account_ids)
        accs = [a for a in accs if a.id in _id_set]

    total = len(accs)
    _add_job_log(job.id, f"전체마켓 주문수집 시작 ({total}개 계정, 최근 {days}일)")
    job.total = total
    job.current = 0
    job.progress = 0
    session.add(job)
    await session.flush()

    # 라우터 함수 직접 호출(Depends 우회) — 라우터 변경 0
    from backend.api.v1.routers.samba.order import (
        sync_orders_from_markets,
        SyncOrdersRequest,
    )
    from backend.db.orm import get_write_session

    total_synced = 0
    all_results: list[dict[str, Any]] = []
    per_account_timeout = _per_account_timeout_seconds(days)

    for idx, acc in enumerate(accs):
        # 사용자 취소 체크 — 매 계정 시작 전
        if await repo.is_cancelled(job.id):
            logger.info(f"[order_sync] {job.id} 취소 감지 — 중단")
            _add_job_log(job.id, "사용자 취소 — 동기화 중단")
            return

        label = f"{acc.market_name}({acc.seller_id or '-'})"
        _add_job_log(
            job.id,
            f"{label}: 주문수집 시작 ({idx + 1}/{total}, 최근 {days}일, 제한 {per_account_timeout}초)",
        )
        try:
            # 계정마다 독립 세션 — 앞 계정의 commit/rollback 잔류 상태로 인한 오염 차단
            # (특히 스마트스토어 분기는 last-changed-statuses 13×2 호출로 26초+ 걸려
            #  공유 세션에서 트랜잭션 abort 시 후속 분기들이 silent 실패하는 사고가 있었다)
            async with get_write_session() as acc_session:
                try:
                    res = await asyncio.wait_for(
                        sync_orders_from_markets(
                            body=SyncOrdersRequest(days=days, account_id=acc.id),
                            session=acc_session,
                            tenant_id=job.tenant_id,
                        ),
                        timeout=per_account_timeout,
                    )
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    # asyncpg + CancelledError 좀비 차단 — async with 의 자동 rollback 만으로는
                    # idle in transaction 으로 남는 사례가 있어 명시적으로 5초 안에 rollback 강제.
                    try:
                        await asyncio.wait_for(acc_session.rollback(), timeout=5)
                    except Exception as _rb_err:
                        logger.warning(
                            f"[order_sync] {label} TimeoutError 후 명시적 rollback 실패: {_rb_err}"
                        )
                    raise
            total_synced += int(res.get("total_synced") or 0)
            results = res.get("results") or []
            for r in results:
                all_results.append(r)
                if r.get("status") == "success":
                    _add_job_log(
                        job.id,
                        f"{r.get('account', label)}: "
                        f"{r.get('fetched', 0)}건 조회, "
                        f"{r.get('synced', 0)}건 신규 저장",
                    )
                elif r.get("status") == "skip":
                    _add_job_log(
                        job.id, f"{r.get('account', label)}: {r.get('message', '')}"
                    )
                else:
                    _add_job_log(
                        job.id,
                        f"{r.get('account', label)}: 오류 — {r.get('message', '')}",
                    )
        except asyncio.TimeoutError:
            logger.error(f"[order_sync] {label} timeout after {per_account_timeout}s")
            _add_job_log(
                job.id,
                f"{label} 오류: {per_account_timeout}초 동안 응답이 없어 다음 계정으로 넘어갑니다",
            )
            all_results.append(
                {
                    "account": label,
                    "status": "error",
                    "message": f"timeout after {per_account_timeout}s",
                }
            )
        except Exception as e:
            logger.error(f"[order_sync] {label} 실패: {e}")
            _add_job_log(job.id, f"{label} 오류: {e}")
            all_results.append(
                {"account": label, "status": "error", "message": str(e)[:500]}
            )
            # acc_session 은 컨텍스트 매니저가 자체 rollback — 워커 세션은 노출 안 됐으므로 별도 롤백 불필요

        # 진행률 갱신 — 매 계정 처리 후 fresh 세션 + 5초 타임아웃
        # 워커 세션이 풀 압박/idle in transaction 으로 hang 시
        # "주문수집 중..." 무한 표시되는 사고 방지
        try:
            async with get_write_session() as prog_session:
                prog_repo = SambaJobRepository(prog_session)
                await asyncio.wait_for(
                    prog_repo.update_progress(job.id, idx + 1, total),
                    timeout=5,
                )
                await prog_session.commit()
        except (asyncio.TimeoutError, Exception) as pe:
            logger.warning(f"[order_sync] {job.id} 진행률 갱신 실패 (계속 진행): {pe}")

    _add_job_log(job.id, f"전체마켓 주문수집 완료 — 총 {total_synced}건 신규 저장")

    # 잡 완료 — 워커 세션이 idle in transaction/풀 락으로 hang 되면 status가
    # 영원히 'running' 으로 남아 프론트가 "주문수집 중..." 무한 표시되는 사고가 있어
    # 독립된 fresh 세션에서 즉시 commit (워커 세션과 분리)
    from backend.domain.samba.job.repository import SambaJobRepository as _Repo

    try:
        async with get_write_session() as fin_session:
            fin_repo = _Repo(fin_session)
            await asyncio.wait_for(
                fin_repo.complete_job(
                    job.id,
                    result={"total_synced": total_synced, "results": all_results},
                ),
                timeout=10,
            )
            await fin_session.commit()
    except Exception as fe:
        logger.error(
            f"[order_sync] {job.id} 최종 commit 실패 — 워커 세션 fallback: {fe}"
        )
        # fallback: 워커 세션 — finally 에서 commit 시도
        await repo.complete_job(
            job.id,
            result={"total_synced": total_synced, "results": all_results},
        )
