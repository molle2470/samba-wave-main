from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, Optional

import httpx
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from backend.domain.samba.forbidden.model import SambaSettings
from backend.utils.logger import logger

SUPPORTED_CURRENCIES = ("USD", "JPY", "CNY", "EUR")

CURRENCY_LABELS: dict[str, str] = {
    "USD": "달러",
    "JPY": "엔화",
    "CNY": "위안화",
    "EUR": "유로화",
}

SOURCE_SITE_CURRENCY_MAP: dict[str, str] = {
    "amazon": "USD",
    "ebay": "USD",
    "shopify": "USD",
    "lazada": "USD",
    "shopee": "USD",
    "qoo10": "USD",
    "rakuten": "JPY",
    "buyma": "JPY",
    "poizon": "CNY",
    "zoom": "CNY",
    "farfetch": "EUR",
}

EXCHANGE_RATE_SETTING_KEY = "exchange_rates"
EXCHANGE_RATE_PROVIDER = "open.er-api.com"
EXCHANGE_RATE_TTL = timedelta(hours=1)

_exchange_rate_cache: dict[str, Any] | None = None
_exchange_rate_cache_expires_at: datetime | None = None
_exchange_rate_cache_lock = asyncio.Lock()


def get_default_exchange_settings() -> dict[str, Any]:
    return {
        "currencies": {
            code: {"adjustment": 0, "fixedRate": 0} for code in SUPPORTED_CURRENCIES
        }
    }


def normalize_exchange_settings(raw: Any) -> dict[str, Any]:
    settings = get_default_exchange_settings()
    if not isinstance(raw, dict):
        return settings

    raw_currencies = raw.get("currencies")
    if not isinstance(raw_currencies, dict):
        raw_currencies = raw

    for code in SUPPORTED_CURRENCIES:
        item = raw_currencies.get(code, {})
        if not isinstance(item, dict):
            item = {}
        settings["currencies"][code] = {
            "adjustment": _to_number(item.get("adjustment", 0)),
            "fixedRate": _to_number(item.get("fixedRate", 0)),
        }
    return settings


def resolve_currency_for_source_site(source_site: str | None) -> Optional[str]:
    if not source_site:
        return None
    return SOURCE_SITE_CURRENCY_MAP.get(source_site.strip().lower())


def _to_number(value: Any) -> float:
    try:
        if value in ("", None):
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


async def get_exchange_rate_settings(
    session: AsyncSession, tenant_id: str | None = None
) -> dict[str, Any]:
    effective_key = (
        f"{tenant_id}:{EXCHANGE_RATE_SETTING_KEY}"
        if tenant_id
        else EXCHANGE_RATE_SETTING_KEY
    )
    stmt = select(SambaSettings).where(SambaSettings.key == effective_key)
    result = await session.execute(stmt)
    row = result.scalars().first()
    val = row.value if row else None
    try:
        await session.commit()
    except Exception:
        pass
    return normalize_exchange_settings(val)


async def get_latest_exchange_rates(force_refresh: bool = False) -> dict[str, Any]:
    global _exchange_rate_cache, _exchange_rate_cache_expires_at

    now = datetime.now(UTC)
    if (
        not force_refresh
        and _exchange_rate_cache is not None
        and _exchange_rate_cache_expires_at is not None
        and _exchange_rate_cache_expires_at > now
    ):
        return _exchange_rate_cache

    async with _exchange_rate_cache_lock:
        now = datetime.now(UTC)
        if (
            not force_refresh
            and _exchange_rate_cache is not None
            and _exchange_rate_cache_expires_at is not None
            and _exchange_rate_cache_expires_at > now
        ):
            return _exchange_rate_cache

        payload = await _fetch_latest_exchange_rates()
        _exchange_rate_cache = payload
        _exchange_rate_cache_expires_at = now + EXCHANGE_RATE_TTL
        return payload


async def _fetch_latest_exchange_rates() -> dict[str, Any]:
    url = f"https://{EXCHANGE_RATE_PROVIDER}/v6/latest/USD"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()

        rates = data.get("rates") or {}
        usd_to_krw = _to_number(rates.get("KRW"))
        usd_to_jpy = _to_number(rates.get("JPY"))
        usd_to_cny = _to_number(rates.get("CNY"))
        usd_to_eur = _to_number(rates.get("EUR"))

        if usd_to_krw <= 0 or usd_to_jpy <= 0 or usd_to_cny <= 0 or usd_to_eur <= 0:
            raise ValueError("exchange rate payload missing required currencies")

        return {
            "provider": EXCHANGE_RATE_PROVIDER,
            "base": "KRW",
            "fetchedAt": datetime.now(UTC).isoformat(),
            "publishedAt": data.get("time_last_update_utc"),
            "rates": {
                "USD": usd_to_krw,
                "JPY": usd_to_krw / usd_to_jpy,
                "CNY": usd_to_krw / usd_to_cny,
                "EUR": usd_to_krw / usd_to_eur,
            },
        }
    except Exception as exc:
        logger.warning(f"[exchange-rate] latest fetch failed: {exc}")
        if _exchange_rate_cache is not None:
            return _exchange_rate_cache
        raise


def build_exchange_rate_response(
    settings: dict[str, Any], latest_rates: dict[str, Any]
) -> dict[str, Any]:
    currencies: dict[str, Any] = {}
    latest_map = latest_rates.get("rates") or {}
    setting_map = settings.get("currencies") or {}

    for code in SUPPORTED_CURRENCIES:
        item = setting_map.get(code, {})
        base_rate = _to_number(latest_map.get(code))
        adjustment = _to_number(item.get("adjustment", 0))
        fixed_rate = _to_number(item.get("fixedRate", 0))
        use_fixed = fixed_rate > 0
        effective_rate = fixed_rate if use_fixed else max(base_rate + adjustment, 0)
        currencies[code] = {
            "code": code,
            "label": CURRENCY_LABELS[code],
            "baseRate": base_rate,
            "adjustment": adjustment,
            "fixedRate": fixed_rate,
            "effectiveRate": effective_rate,
            "useFixed": use_fixed,
        }

    return {
        "provider": latest_rates.get("provider", EXCHANGE_RATE_PROVIDER),
        "base": "KRW",
        "fetchedAt": latest_rates.get("fetchedAt"),
        "publishedAt": latest_rates.get("publishedAt"),
        "currencies": currencies,
    }


async def convert_cost_by_source_site(
    session: AsyncSession,
    cost: float,
    source_site: str | None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    currency = resolve_currency_for_source_site(source_site)
    if not currency or cost <= 0:
        return {
            "currency": currency,
            "baseCost": cost,
            "convertedCost": cost,
            "rateApplied": 1.0,
            "exchangeApplied": False,
        }

    settings = await get_exchange_rate_settings(session, tenant_id)
    latest_rates = await get_latest_exchange_rates()
    exchange_data = build_exchange_rate_response(settings, latest_rates)
    currency_data = exchange_data["currencies"][currency]
    effective_rate = _to_number(currency_data.get("effectiveRate"))

    if effective_rate <= 0:
        return {
            "currency": currency,
            "baseCost": cost,
            "convertedCost": cost,
            "rateApplied": 1.0,
            "exchangeApplied": False,
        }

    return {
        "currency": currency,
        "baseCost": cost,
        "convertedCost": round(cost * effective_rate),
        "rateApplied": effective_rate,
        "exchangeApplied": True,
    }
