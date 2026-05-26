# -*- coding: utf-8 -*-
"""무신사 상품 이미지 가공 상태 집계 — 원본(msscdn) vs samba 프록시(가공) 비율.
읽기 전용.
"""
import asyncio
import json

from sqlmodel import select

from backend.db.orm import get_read_session
from backend.domain.samba.collector.model import SambaCollectedProduct as CP


async def main():
    async with get_read_session() as session:
        stmt = (
            select(CP.id, CP.brand, CP.category, CP.images, CP.detail_html, CP.tags)
            .where(CP.source_site == "MUSINSA")
            .limit(2000)
        )
        rows = (await session.execute(stmt)).all()
    total = len(rows)
    img_proxy = 0
    img_msscdn = 0
    img_none = 0
    detail_proxy = 0
    detail_msscdn = 0
    proxied_samples = []
    for r in rows:
        imgs = r.images or []
        first = str(imgs[0]) if imgs else ""
        if not first:
            img_none += 1
        elif "samba-wave" in first or "r2." in first or "cloudflarestorage" in first:
            img_proxy += 1
            if len(proxied_samples) < 8:
                proxied_samples.append(
                    {
                        "id": r.id,
                        "brand": (r.brand or "").strip(),
                        "category": (r.category or "").strip(),
                        "img0": first[:100],
                        "tags": r.tags,
                    }
                )
        elif "msscdn" in first:
            img_msscdn += 1
        dh = r.detail_html or ""
        if "samba-wave" in dh:
            detail_proxy += 1
        if "msscdn" in dh:
            detail_msscdn += 1
    print(
        json.dumps(
            {
                "total_musinsa": total,
                "img0_samba_proxy(가공)": img_proxy,
                "img0_msscdn(원본)": img_msscdn,
                "img0_none": img_none,
                "detail_html_has_samba_proxy": detail_proxy,
                "detail_html_has_msscdn": detail_msscdn,
                "proxied_samples": proxied_samples,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
