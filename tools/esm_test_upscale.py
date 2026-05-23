# -*- coding: utf-8 -*-
"""mirror_oversized_to_r2(min_dim=600) 검증 — msscdn 500px → R2 ≥600 업스케일 확인. 읽기성."""
import asyncio
import io
import json

import httpx
from PIL import Image

from backend.db.orm import get_read_session
from backend.domain.samba.collector.repository import SambaCollectedProductRepository
from backend.domain.samba.image.service import ImageTransformService

PID = "cp_01KMXZ8NJ1Q4CE2DH67PAG7Q7Q"


async def dim(url):
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(url)
            im = Image.open(io.BytesIO(r.content))
            return f"{im.width}x{im.height} ({len(r.content)}B)"
    except Exception as e:
        return f"ERR {type(e).__name__}: {str(e)[:60]}"


async def main():
    async with get_read_session() as s:
        p = await SambaCollectedProductRepository(s).get_async(PID)
        imgs = (p.images or [])[:3]
        svc = ImageTransformService(s)
        out = {"orig": [], "mirrored": []}
        for u in imgs:
            out["orig"].append({"url": u[:70], "dim": await dim(u)})
        new, mp = await svc.mirror_oversized_to_r2(imgs, min_dim=600)
        for u in new:
            out["mirrored"].append({"url": u[:80], "dim": await dim(u)})
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
