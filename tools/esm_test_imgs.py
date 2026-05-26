import asyncio, io, json, sys
import httpx
from PIL import Image
from backend.db.orm import get_read_session
from backend.domain.samba.collector.repository import SambaCollectedProductRepository
from backend.domain.samba.image.service import ImageTransformService
PID=sys.argv[1]
async def dim(u):
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r=await c.get(u); im=Image.open(io.BytesIO(r.content)); return f"{im.width}x{im.height} {r.status_code}"
    except Exception as e: return f"ERR {type(e).__name__} {str(e)[:50]}"
async def main():
    async with get_read_session() as s:
        p=await SambaCollectedProductRepository(s).get_async(PID)
        imgs=p.images or []
        svc=ImageTransformService(s)
        o={"brand":p.brand,"cat":p.category,"n":len(imgs),"orig":[],"mirror":[]}
        for u in imgs[:4]: o["orig"].append({"u":u[:60],"d":await dim(u)})
        new,_=await svc.mirror_oversized_to_r2(imgs,min_dim=600)
        for u in new[:4]: o["mirror"].append({"u":u[:70],"blocked":ImageTransformService.is_hotlink_blocked_url(u)})
    print(json.dumps(o,ensure_ascii=False,indent=2,default=str))
asyncio.run(main())
