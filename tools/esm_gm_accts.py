import asyncio, json
from sqlmodel import select
from backend.db.orm import get_read_session
from backend.domain.samba.account.model import SambaMarketAccount as A
async def main():
    async with get_read_session() as s:
        rows=(await s.execute(select(A).where(A.market_type=="gmarket"))).scalars().all()
        out=[{"id":a.id,"label":a.account_label,"seller_id":(a.seller_id or "")[:3]+"***","active":a.is_active,"tenant":a.tenant_id} for a in rows]
    print(json.dumps({"gmarket_accounts":out,"n":len(out)},ensure_ascii=False,indent=2,default=str))
asyncio.run(main())
