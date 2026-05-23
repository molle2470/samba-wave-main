# -*- coding: utf-8 -*-
"""ESM 택배사 코드 조회 엔드포인트 probe — 읽기 전용. 롯데택배 정식 코드 확보용."""
import asyncio
import json

from backend.db.orm import get_read_session
from backend.domain.samba.account.repository import SambaMarketAccountRepository
from backend.domain.samba.proxy.esmplus import ESMPlusClient, resolve_esm_credentials

GMARKET_ID = "ma_01KN1DEKNZYW38RDPFVW7VNYCY"

CANDIDATES = [
    ("GET", "/item/v1/codes/delivery-companies"),
    ("GET", "/item/v1/delivery-companies"),
    ("GET", "/item/v1/shipping/delivery-companies"),
    ("GET", "/item/v1/codes/delivery"),
    ("GET", "/item/v1/codes"),
    ("GET", "/shipping/v1/Code/DeliveryCompany"),
    ("GET", "/shipping/v1/codes/delivery-companies"),
    ("GET", "/item/v1/shipping/companies"),
]


async def main():
    async with get_read_session() as session:
        repo = SambaMarketAccountRepository(session)
        a = await repo.get_async(GMARKET_ID)
        extras = a.additional_fields or {}
        creds = {k: v for k, v in extras.items() if v}
        seller_id = (
            creds.get("apiKey", "")
            or creds.get("sellerId", "")
            or (a.seller_id or "")
        )
        hosting_id, secret_key = await resolve_esm_credentials(session, a)
    client = ESMPlusClient(hosting_id, secret_key, seller_id, site="gmarket")
    results = {}
    try:
        for method, path in CANDIDATES:
            try:
                r = await client._call_api(method, path)
                # 응답 요약 — 롯데/롯데택배 포함 여부 + 키
                txt = json.dumps(r, ensure_ascii=False)
                results[path] = {
                    "ok": True,
                    "keys": list(r.keys()) if isinstance(r, dict) else type(r).__name__,
                    "has_lotte": "롯데" in txt,
                    "snippet": txt[:600],
                }
            except Exception as e:
                results[path] = {"ok": False, "err": f"{type(e).__name__}: {str(e)[:120]}"}
    finally:
        await client.aclose()
    print(json.dumps(results, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
