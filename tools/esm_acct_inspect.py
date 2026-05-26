# -*- coding: utf-8 -*-
"""ESM(G마켓/옥션) 계정 인증필드 점검 — creds 구성 확인. 읽기 전용."""
import asyncio
import json

from backend.db.orm import get_read_session
from backend.domain.samba.account.repository import SambaMarketAccountRepository

IDS = ["ma_01KN1DEKNZYW38RDPFVW7VNYCY", "ma_01KN1DH6ZZW7A2SQWB9HTRGF0H"]


def _mask(v):
    if not v:
        return None
    s = str(v)
    return s[:3] + "***" + s[-2:] if len(s) > 6 else s[0] + "***"


async def main():
    out = []
    async with get_read_session() as session:
        repo = SambaMarketAccountRepository(session)
        for aid in IDS:
            a = await repo.get_async(aid)
            if not a:
                out.append({"id": aid, "error": "없음"})
                continue
            extras = a.additional_fields or {}
            out.append(
                {
                    "id": aid,
                    "market_type": a.market_type,
                    "account_label": a.account_label,
                    "seller_id_col": _mask(a.seller_id),
                    "api_key_col": _mask(a.api_key),
                    "api_secret_col": _mask(a.api_secret),
                    "additional_keys": list(extras.keys()),
                    "additional_has_apiKey": bool(extras.get("apiKey")),
                    "additional_has_sellerId": bool(extras.get("sellerId")),
                    "additional_apiKey_val": _mask(extras.get("apiKey")),
                    "additional_sellerId_val": _mask(extras.get("sellerId")),
                }
            )
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
