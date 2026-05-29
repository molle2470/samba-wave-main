"""쿠팡 v4 ordersheets 실제 응답에서 수량 필드 확정 진단.

orderItems[0]의 모든 키와 shippingCount/orderQuantity/salesPrice 실제 값·타입 덤프.
"""

import asyncio
import json

from sqlalchemy import select

from backend.db.orm import get_write_session
from backend.domain.samba.account.model import SambaMarketAccount
from backend.domain.samba.proxy.coupang import CoupangClient


async def main() -> None:
    async with get_write_session() as session:
        rows = (
            await session.execute(
                select(SambaMarketAccount).where(
                    SambaMarketAccount.market_type == "coupang",
                    SambaMarketAccount.is_active == True,  # noqa: E712
                )
            )
        ).scalars().all()

    if not rows:
        print("[NOT FOUND] 활성 쿠팡 계정 없음")
        return

    for acc in rows:
        extras = acc.additional_fields or {}
        access_key = extras.get("accessKey", "") or acc.api_key or ""
        secret_key = extras.get("secretKey", "") or acc.api_secret or ""
        vendor_id = extras.get("vendorId", "") or acc.seller_id or ""
        if not all([access_key, secret_key, vendor_id]):
            print(f"[SKIP] {acc.market_name}: 인증정보 부족")
            continue

        client = CoupangClient(access_key, secret_key, vendor_id)
        orders = await client.get_orders(days=7)

        print("=" * 80)
        print(f"계정: {acc.market_name} (vendor={vendor_id})  주문 {len(orders)}건")

        # 수량 후보 키가 1보다 큰 주문 우선 탐색
        printed = 0
        for o in orders:
            items = o.get("orderItems") or []
            if not items:
                continue
            it = items[0]
            sc = it.get("shippingCount")
            oq = it.get("orderQuantity")
            # 멀티수량 주문 우선
            multi = (isinstance(sc, int) and sc > 1) or (
                isinstance(oq, int) and oq > 1
            )
            if printed >= 3 and not multi:
                continue
            print("-" * 60)
            print(f"orderId={o.get('orderId')} status={o.get('status')}")
            print(f"  orderItems[0] keys: {sorted(it.keys())}")
            print(f"  shippingCount = {sc!r} (type={type(sc).__name__})")
            print(f"  orderQuantity = {oq!r} (type={type(oq).__name__})")
            print(f"  salesPrice    = {it.get('salesPrice')!r}")
            print(f"  orderPrice    = {it.get('orderPrice')!r}")
            if multi:
                print("  *** 멀티수량 주문 (raw 전체) ***")
                print(json.dumps(it, ensure_ascii=False, indent=2)[:1500])
            printed += 1
            if printed >= 6:
                break


if __name__ == "__main__":
    asyncio.run(main())
