# -*- coding: utf-8 -*-
"""무신사 등록 후보 사전점검 — applied_policy_id + G마켓/옥션 카테고리매핑 보유 +
브랜드/카테고리 상이 3건 선별. 읽기 전용(전송 호출 없음)."""
import asyncio
import json

from sqlmodel import select

from backend.db.orm import get_read_session
from backend.domain.samba.collector.model import SambaCollectedProduct as CP
from backend.domain.samba.shipment.repository import SambaShipmentRepository
from backend.domain.samba.shipment.service import SambaShipmentService

GMARKET_ID = "ma_01KN1DEKNZYW38RDPFVW7VNYCY"
AUCTION_ID = "ma_01KN1DH6ZZW7A2SQWB9HTRGF0H"


async def main():
    async with get_read_session() as session:
        svc = SambaShipmentService(SambaShipmentRepository(session), session)
        stmt = (
            select(
                CP.id,
                CP.name,
                CP.brand,
                CP.category,
                CP.sale_price,
                CP.applied_policy_id,
                CP.status,
                CP.registered_accounts,
            )
            .where(
                CP.source_site == "MUSINSA",
                CP.applied_policy_id.is_not(None),
            )
            .limit(400)
        )
        rows = (await session.execute(stmt)).all()

        qualified = []
        checked = 0
        for r in rows:
            cat = (r.category or "").strip()
            if not cat:
                continue
            checked += 1
            try:
                mapped = await svc._resolve_category_mappings(
                    "MUSINSA", cat, [GMARKET_ID, AUCTION_ID]
                )
            except Exception as e:
                mapped = {"_err": str(e)[:80]}
            g = mapped.get("gmarket")
            a = mapped.get("auction")
            if g and a:
                qualified.append(
                    {
                        "id": r.id,
                        "brand": (r.brand or "").strip(),
                        "category": cat,
                        "sale_price": r.sale_price,
                        "policy": r.applied_policy_id,
                        "status": r.status,
                        "gmarket_cat": g,
                        "auction_cat": a,
                        "reg_accounts": r.registered_accounts,
                    }
                )
            if len(qualified) >= 60:
                break

        # 브랜드 + 카테고리 모두 상이한 3건 선택
        picked = []
        seen_brand = set()
        seen_cat = set()
        for q in qualified:
            b = q["brand"]
            c = q["category"]
            if b in seen_brand or c in seen_cat:
                continue
            picked.append(q)
            seen_brand.add(b)
            seen_cat.add(c)
            if len(picked) >= 3:
                break

    print(
        json.dumps(
            {
                "candidates_with_policy": len(rows),
                "checked_with_category": checked,
                "qualified_both_markets": len(qualified),
                "PICKED_3": picked,
                "qualified_sample": qualified[:12],
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
