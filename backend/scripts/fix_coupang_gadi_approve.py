"""임시저장(SAVED) 상태 상품 → requested=True PUT으로 승인요청 전환."""

import asyncio
import json
import asyncpg
from backend.core.config import settings

ACCOUNT_ID = "ma_01KNZV0ZWXW52W0G4TYG3AJH9Q"
_SERVER_KEYS = {
    "sellerProductId",
    "productId",
    "approvalStatus",
    "statusName",
    "exposedStatusName",
    "createdAt",
    "updatedAt",
}
_ITEM_SERVER_KEYS = {"vendorItemId", "itemId"}


async def get_db_conn():
    return await asyncpg.connect(
        host=settings.write_db_host,
        port=settings.write_db_port,
        user=settings.write_db_user,
        password=settings.write_db_password,
        database=settings.write_db_name,
        ssl="require" if settings.use_db_ssl else None,
    )


async def main():
    conn = await get_db_conn()
    row = await conn.fetchrow(
        "SELECT additional_fields FROM samba_market_account WHERE id = $1", ACCOUNT_ID
    )
    await conn.close()
    extras = (
        json.loads(row["additional_fields"])
        if isinstance(row["additional_fields"], str)
        else (row["additional_fields"] or {})
    )
    access_key = extras.get("accessKey") or ""
    secret_key = extras.get("secretKey") or ""
    vendor_id = extras.get("vendorId") or ""

    from backend.domain.samba.proxy.coupang import CoupangClient

    client = CoupangClient(access_key, secret_key, vendor_id)

    print("SAVED 상품 목록 조회 중...")
    saved = await client.list_seller_products(status="SAVED")
    print(f"SAVED: {len(saved):,}개")

    ok, fail = 0, 0
    for item in saved:
        spid = item["seller_product_id"]
        name = item["product_name"][:40]
        try:
            resp = await client.get_product(spid)
            data = resp.get("data", resp) if "data" in resp else resp
            if not isinstance(data, dict):
                fail += 1
                continue

            # 서버 ID 제거 + requested=True
            put_data = {k: v for k, v in data.items() if k not in _SERVER_KEYS}
            items = put_data.get("items")
            if isinstance(items, list):
                put_data["items"] = [
                    {k: v for k, v in it.items() if k not in _ITEM_SERVER_KEYS}
                    if isinstance(it, dict)
                    else it
                    for it in items
                ]
            put_data["requested"] = True
            put_data["vendorId"] = vendor_id
            put_data["vendorUserId"] = vendor_id

            # PUT 불가(SAVED 상태) → approve_product로 승인요청 전환
            await client.approve_product(spid)
            print(f"  ✓ {spid} ({name})")
            ok += 1
            await asyncio.sleep(0.3)

        except Exception as e:
            print(f"  ✗ {spid} ({name}) — {e}")
            fail += 1

    print(f"\n완료: 성공 {ok:,} / 실패 {fail:,}")


if __name__ == "__main__":
    asyncio.run(main())
