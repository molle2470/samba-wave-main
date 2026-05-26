import asyncio
import sys
from types import ModuleType
from types import SimpleNamespace

from backend.domain.samba.shipment import dispatcher


def _install_dummy_elevenst(monkeypatch, client_cls):
    module = ModuleType("backend.domain.samba.proxy.elevenst")
    module.ElevenstClient = client_cls
    monkeypatch.setitem(sys.modules, "backend.domain.samba.proxy.elevenst", module)


def test_delete_11st_uses_account_additional_fields_api_key(monkeypatch):
    captured: dict[str, str] = {}

    class DummyClient:
        def __init__(self, api_key: str):
            captured["api_key"] = api_key

        async def delete_product(self, product_no: str):
            captured["product_no"] = product_no

    async def fake_get_setting(session, key: str, tenant_id=None):
        return {"apiKey": "GLOBAL_KEY"}

    monkeypatch.setattr(dispatcher, "_get_setting", fake_get_setting)
    # resolver 도 _get_setting 호출 (4-3b 이후) — 같은 fake 로 패치.
    import backend.api.v1.routers.samba.proxy._helpers as _helpers_mod

    monkeypatch.setattr(_helpers_mod, "_get_setting", fake_get_setting)
    _install_dummy_elevenst(monkeypatch, DummyClient)

    account = SimpleNamespace(
        additional_fields={"apiKey": "ACCOUNT_KEY"},
        api_key="",
    )
    product = {"market_product_no": {"11st": "prd-123"}}

    result = asyncio.run(dispatcher._delete_11st(None, product, account=account))

    assert result["success"] is True
    assert captured == {"api_key": "ACCOUNT_KEY", "product_no": "prd-123"}


def test_delete_11st_does_not_fallback_to_global_setting_when_account_is_explicit(
    monkeypatch,
):
    called = {"client_created": False}

    class DummyClient:
        def __init__(self, api_key: str):
            called["client_created"] = True

        async def delete_product(self, product_no: str):
            return None

    async def fake_get_setting(session, key: str, tenant_id=None):
        return {"apiKey": "GLOBAL_KEY"}

    monkeypatch.setattr(dispatcher, "_get_setting", fake_get_setting)
    # resolver 도 _get_setting 호출 (4-3b 이후) — 같은 fake 로 패치.
    import backend.api.v1.routers.samba.proxy._helpers as _helpers_mod

    monkeypatch.setattr(_helpers_mod, "_get_setting", fake_get_setting)
    _install_dummy_elevenst(monkeypatch, DummyClient)

    account = SimpleNamespace(additional_fields={}, api_key="")
    product = {"market_product_no": {"11st": "prd-123"}}

    result = asyncio.run(dispatcher._delete_11st(None, product, account=account))

    assert result == {"success": False, "message": "11번가 인증 정보 없음"}
    assert called["client_created"] is False


def test_delete_11st_no_account_no_fallback(monkeypatch):
    """legacy store_* global fallback 폐기 (2026-05-25) — 계정 없으면 인증 실패.

    이전: samba_settings.store_11st 폴백 → 어떤 계정 키로 삭제 가능 (위험).
    현재: samba_market_account 단일 진실 출처. 계정 없으면 항상 실패.
    """
    called = {"client_created": False}

    class DummyClient:
        def __init__(self, api_key: str):
            called["client_created"] = True

        async def delete_product(self, product_no: str):
            return None

    _install_dummy_elevenst(monkeypatch, DummyClient)

    product = {"market_product_no": {"11st": "prd-123"}}

    result = asyncio.run(dispatcher._delete_11st(None, product, account=None))

    assert result == {"success": False, "message": "11번가 인증 정보 없음"}
    assert called["client_created"] is False
