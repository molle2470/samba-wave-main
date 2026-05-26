import asyncio, json
from backend.db.orm import get_read_session
from backend.domain.samba.account.repository import SambaMarketAccountRepository
from backend.domain.samba.proxy.esmplus import ESMPlusClient, resolve_esm_credentials
AUC="ma_01KN1DH6ZZW7A2SQWB9HTRGF0H"
async def main():
    async with get_read_session() as s:
        a=await SambaMarketAccountRepository(s).get_async(AUC)
        e=a.additional_fields or {}; creds={k:v for k,v in e.items() if v}
        sid=creds.get("apiKey","") or creds.get("sellerId","") or (a.seller_id or "")
        h,sec=await resolve_esm_credentials(s,a)
    c=ESMPlusClient(h,sec,sid,site="auction")
    try:
        r=await c.search_products({"pageIndex":1,"pageSize":10})
        items=r.get("items") or []
        print(json.dumps({"totalItems":r.get("totalItems"),"count":len(items),
            "goodsNos":[ (it.get("goodsNo") or it.get("GoodsNo")) for it in items]},ensure_ascii=False))
    finally:
        await c.aclose()
asyncio.run(main())
