# -*- coding: utf-8 -*-
"""G마켓 계정 shippingCompanyNo 를 ESM 정식 코드(롯데택배 10008)로 교정. WRITE."""
import asyncio
import json

from sqlalchemy.orm.attributes import flag_modified

from backend.db.orm import get_write_session
from backend.domain.samba.account.repository import SambaMarketAccountRepository

GMARKET_ID = "ma_01KN1DEKNZYW38RDPFVW7VNYCY"
NEW_CODE = "10008"  # ESM deliveryCompCode 롯데택배


async def main():
    async with get_write_session() as session:
        repo = SambaMarketAccountRepository(session)
        a = await repo.get_async(GMARKET_ID)
        before = (a.additional_fields or {}).get("shippingCompanyNo")
        extras = dict(a.additional_fields or {})
        extras["shippingCompanyNo"] = NEW_CODE
        a.additional_fields = extras
        flag_modified(a, "additional_fields")
        session.add(a)
        await session.commit()
        await session.refresh(a)
        after = (a.additional_fields or {}).get("shippingCompanyNo")
        print(json.dumps({"id": GMARKET_ID, "before": before, "after": after}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
