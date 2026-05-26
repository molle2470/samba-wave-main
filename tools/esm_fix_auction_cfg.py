# -*- coding: utf-8 -*-
"""옥션 계정 배송 config 보강 — dispatchPolicyNo/place/택배사 설정. WRITE.
값 출처: Phase1 get_places(23731264) / get_dispatch_policies(1997210 default)."""
import asyncio
import json

from sqlalchemy.orm.attributes import flag_modified

from backend.db.orm import get_write_session
from backend.domain.samba.account.repository import SambaMarketAccountRepository

AUCTION_ID = "ma_01KN1DH6ZZW7A2SQWB9HTRGF0H"
UPDATES = {
    "dispatchPolicyNo": "1997210",
    "shippingPlaceNo": "23731264",
    "returnPlaceNo": "23731264",
    "shippingCompanyNo": "10008",  # ESM 롯데택배
}


async def main():
    async with get_write_session() as session:
        repo = SambaMarketAccountRepository(session)
        a = await repo.get_async(AUCTION_ID)
        before = dict(a.additional_fields or {})
        extras = dict(a.additional_fields or {})
        extras.update(UPDATES)
        a.additional_fields = extras
        flag_modified(a, "additional_fields")
        session.add(a)
        await session.commit()
        await session.refresh(a)
        print(
            json.dumps(
                {"id": AUCTION_ID, "before": before, "after": a.additional_fields},
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )


if __name__ == "__main__":
    asyncio.run(main())
