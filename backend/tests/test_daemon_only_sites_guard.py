"""DAEMON_ONLY_SITES (SSG/ABCmart/GrandStage/LOTTEON) 영구 차단 회귀 테스트.

3중 장벽 검증:
- 발행 가드: 5개 add_*_job 함수 (search/detail/tracking/reward/cancel_order)
- dequeue 가드: get_next_job SQL 비데몬 dev 차단
- 등록 가드: register_pc_allowed_sites 비데몬 dev strip
"""

from __future__ import annotations

import asyncio
import pytest


# ── 발행 가드 — 5개 add_*_job 함수 × 4개 사이트 데몬 미등록 → RuntimeError ─────


@pytest.fixture(autouse=True)
def _stub_daemon_pool_empty(monkeypatch):
    """모든 테스트에서 데몬 풀 비어있게 stub — pick_daemon_owner() = None."""
    monkeypatch.setattr(
        "backend.domain.samba.proxy.daemon_pool.pick_daemon_owner",
        lambda site: None,
    )
    monkeypatch.setattr(
        "backend.domain.samba.proxy.sourcing_queue.get_autotune_owner",
        lambda site: "extension-fake-dev",
    )


@pytest.fixture
def _stub_db_insert(monkeypatch):
    """_db_insert_job 호출 무력화 — DB 의존 X."""

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "backend.domain.samba.proxy.sourcing_queue._db_insert_job", _noop
    )


@pytest.mark.parametrize("site", ["SSG", "ABCmart", "GrandStage", "LOTTEON"])
def test_add_search_job_blocks_daemon_only(site, _stub_db_insert):
    from backend.domain.samba.proxy.sourcing_queue import SourcingQueue

    async def _run():
        with pytest.raises(RuntimeError, match="데몬 미등록"):
            SourcingQueue.add_search_job(site, "테스트 키워드")

    asyncio.run(_run())


@pytest.mark.parametrize("site", ["SSG", "ABCmart", "GrandStage", "LOTTEON"])
def test_add_detail_job_blocks_daemon_only(site, _stub_db_insert):
    from backend.domain.samba.proxy.sourcing_queue import SourcingQueue

    async def _run():
        with pytest.raises(RuntimeError, match="데몬 미등록"):
            SourcingQueue.add_detail_job(site, "test-product-id")

    asyncio.run(_run())


@pytest.mark.parametrize(
    "site",
    [
        "SSG",
        "ABCmart",
        "GrandStage",
        "LOTTEON",
        "MUSINSA",
        "GSShop",
        "FashionPlus",
        "Nike",
        "OliveYoung",
        "KREAM",
    ],
)
def test_add_tracking_job_blocks_daemon_only(site, _stub_db_insert):
    """송장 잡은 11개 사이트 모두 데몬 전용 — 가드 검증."""
    from backend.domain.samba.proxy.sourcing_queue import SourcingQueue

    async def _run():
        with pytest.raises(RuntimeError, match="데몬 미등록"):
            await SourcingQueue.add_tracking_job(
                site, "https://example.com", "order-1", "ord-num-1"
            )

    asyncio.run(_run())


@pytest.mark.parametrize("site", ["SSG", "ABCmart", "LOTTEON"])
def test_add_reward_job_blocks_daemon_only(site, monkeypatch, _stub_db_insert):
    from backend.domain.samba.proxy.sourcing_queue import SourcingQueue

    # REWARD_ACTION_URLS 에서 사이트별 action 매핑 필요 — 직접 적절한 action 선택
    action_map = {
        "SSG": "ssg_review",
        "ABCmart": "abcmart_attendance",
        "LOTTEON": "lotteon_review",
    }

    async def _run():
        with pytest.raises(RuntimeError, match="데몬 미등록"):
            await SourcingQueue.add_reward_job(site, action_map[site], "acct-test-1")

    asyncio.run(_run())


# LOTTEON cancel_order 는 확장앱 라우팅으로 전환(2026-05-26) — 데몬 전용 가드 제외.
@pytest.mark.parametrize("site", ["SSG", "ABCmart", "GrandStage"])
def test_add_cancel_order_job_blocks_daemon_only(site, _stub_db_insert):
    from backend.domain.samba.proxy.sourcing_queue import SourcingQueue

    async def _run():
        with pytest.raises(RuntimeError, match="데몬 미등록"):
            await SourcingQueue.add_cancel_order_job(
                site, "ord-num-test", "order-internal-1"
            )

    asyncio.run(_run())


# ── 등록 가드 — register_pc_allowed_sites 비데몬 dev strip ─────────────────────


def test_register_strips_daemon_only_for_extension_dev():
    from backend.api.v1.routers.samba.collector_autotune import (
        _pc_allowed_sites,
        get_pc_allowed_sites,
        register_pc_allowed_sites,
    )

    _pc_allowed_sites.pop("ext-test-1", None)
    register_pc_allowed_sites("ext-test-1", ["SSG", "MUSINSA", "LOTTEON", "GSShop"])
    sites = get_pc_allowed_sites("ext-test-1")
    assert sites == {"MUSINSA", "GSShop"}, f"SSG/LOTTEON strip 안 됨: {sites}"


def test_register_preserves_daemon_only_for_daemon_dev():
    from backend.api.v1.routers.samba.collector_autotune import (
        _pc_allowed_sites,
        get_pc_allowed_sites,
        register_pc_allowed_sites,
    )

    _pc_allowed_sites.pop("samba-daemon-test-xyz", None)
    register_pc_allowed_sites(
        "samba-daemon-test-xyz",
        ["SSG", "ABCmart", "LOTTEON"],
        authoritative=True,
    )
    sites = get_pc_allowed_sites("samba-daemon-test-xyz")
    assert sites == {"SSG", "ABCmart", "LOTTEON"}, (
        f"데몬 dev 분담이 strip 됨(잘못): {sites}"
    )
