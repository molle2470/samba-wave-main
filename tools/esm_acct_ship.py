import asyncio, json
from backend.db.orm import get_read_session
from backend.domain.samba.account.repository import SambaMarketAccountRepository
IDS=["ma_01KN1DEKNZYW38RDPFVW7VNYCY","ma_01KN1DH6ZZW7A2SQWB9HTRGF0H"]
async def main():
    async with get_read_session() as s:
        r=SambaMarketAccountRepository(s)
        out=[]
        for aid in IDS:
            a=await r.get_async(aid); e=a.additional_fields or {}
            out.append({"id":aid,"mt":a.market_type,
                "shippingCompanyNo":e.get("shippingCompanyNo"),
                "shippingPlaceNo":e.get("shippingPlaceNo"),
                "returnPlaceNo":e.get("returnPlaceNo"),
                "dispatchPolicyNo":e.get("dispatchPolicyNo")})
        print(json.dumps(out,ensure_ascii=False,indent=2,default=str))
asyncio.run(main())
