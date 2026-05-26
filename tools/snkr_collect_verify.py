"""포켓몬 카드 3개 실제 수집(저장)+검증 — 워커 저장 경로(create_collected_product) 동일.

prod 컨테이너에서 실행. samba_collected_product 에 실제 저장 후 등급별 옵션 조회.
"""

import asyncio

from sqlalchemy import text

from backend.api.v1.routers.samba.collector_common import _get_services
from backend.db.orm import get_write_session
from backend.domain.samba.proxy.snkrdunk import SnkrdunkClient

FILTER_ID = "sf_01KRX46657SDR30H3EPZE0VE3J"
# 사용자가 알려준 포켓몬 카드 (671486=첫 메시지, 618446=스크린샷) + 824237(M5 포켓몬, 재고확인됨)
CARDS = ["671486", "618446", "824237"]


def _norm_stock(o):
    s = o.get("stock") or 0
    return o.get("stock", 0) if s > 1 else (99 if s > 0 else 0)


async def main():
    async with get_write_session() as session:
        row = (
            await session.execute(
                text("SELECT tenant_id FROM samba_search_filter WHERE id=:i"),
                {"i": FILTER_ID},
            )
        ).first()
        tenant_id = row[0] if row else None
        print(f"filter tenant_id={tenant_id}")

        svc = _get_services(session)
        client = SnkrdunkClient()

        for cid in CARDS:
            d = await client.get_detail(cid, "trading-card")
            opts = d.get("options") or []
            if not opts:
                print(f"  {cid}: 옵션 0개(재고없음) → skip")
                continue
            data = {
                "source_site": "SNKRDUNK",
                "search_filter_id": FILTER_ID,
                "tenant_id": tenant_id,
                "site_product_id": cid,
                "name": d.get("name") or "",
                "brand": "",
                "sale_price": d.get("sale_price"),
                "original_price": d.get("original_price"),
                "cost": d.get("sale_price"),
                "images": d.get("images") or [],
                "options": [{**o, "stock": _norm_stock(o)} for o in opts],
                "category": d.get("category"),
                "category1": "SNKRDUNK",
                "category2": d.get("category2"),
                "category3": "",
                "source_url": d.get("url"),
                "video_url": d.get("url"),
                "style_code": d.get("style_code"),
                "detail_html": "",
                "sale_status": d.get("sale_status"),
                "free_shipping": False,
                "color": "",
                "sex": "남녀공용",
                "season": "사계절",
                "status": "collected",
                "group_key": f"snkr_{cid}",
                "extra_data": d.get("extra_data"),
            }
            try:
                saved = await svc.create_collected_product(data)
                print(
                    f"  {cid}: {('저장OK' if saved else 'None(차단/중복)')} "
                    f"name={d.get('name')[:28]!r} 등급 {len(opts)}개 최저 {d.get('sale_price')}"
                )
            except Exception as e:
                print(f"  {cid}: 저장실패 {e!r}")
        await session.commit()

        print("\n=== prod DB 저장 확인 (SNKRDUNK) ===")
        rows = (
            await session.execute(
                text(
                    "SELECT site_product_id, name, sale_price, sale_status, options, "
                    "style_code FROM samba_collected_product WHERE source_site='SNKRDUNK' "
                    "ORDER BY created_at DESC LIMIT 5"
                )
            )
        ).fetchall()
        for r in rows:
            opts = r[4]
            n = len(opts) if isinstance(opts, list) else 0
            print(
                f"  {r[0]} 품번={r[5]!r} {str(r[1])[:24]!r} "
                f"sale={r[2]}(달러) status={r[3]} 등급={n}개"
            )
            if isinstance(opts, list):
                for o in opts[:5]:
                    print(
                        f"       {o.get('name')} ${o.get('price')} stock {o.get('stock')}"
                    )


asyncio.run(main())
