# -*- coding: utf-8 -*-
"""ESM 택배사 리스트 조회 — GET /item/v1/shipping/delivery-company. 읽기 전용."""
import asyncio
import json

from backend.db.orm import get_read_session
from backend.domain.samba.account.repository import SambaMarketAccountRepository
from backend.domain.samba.proxy.esmplus import ESMPlusClient, resolve_esm_credentials

GMARKET_ID = "ma_01KN1DEKNZYW38RDPFVW7VNYCY"
PATHS = [
    "/item/v1/shipping/delivery-company",
    "/item/v1/shipping/delivery-companies",
]


async def main():
    async with get_read_session() as session:
        repo = SambaMarketAccountRepository(session)
        a = await repo.get_async(GMARKET_ID)
        extras = a.additional_fields or {}
        creds = {k: v for k, v in extras.items() if v}
        seller_id = creds.get("apiKey", "") or creds.get("sellerId", "") or (a.seller_id or "")
        hosting_id, secret_key = await resolve_esm_credentials(session, a)
    client = ESMPlusClient(hosting_id, secret_key, seller_id, site="gmarket")
    out = {}
    try:
        for p in PATHS:
            try:
                r = await client._call_api("GET", p)
                lst = r.get("deliveryCompanies") or r.get("data") or r
                out[p] = {"ok": True, "raw_keys": list(r.keys()) if isinstance(r, dict) else type(r).__name__, "body": r}
                break
            except Exception as e:
                out[p] = {"ok": False, "err": f"{type(e).__name__}: {str(e)[:150]}"}
    finally:
        await client.aclose()
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
