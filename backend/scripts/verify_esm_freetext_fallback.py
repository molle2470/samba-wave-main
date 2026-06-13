# -*- coding: utf-8 -*-
"""#413 fallback dry-run 검증 — PUT 없이 매핑/payload 정확성만 확인.

update_existing_freetext_stock 의 매칭 로직(_ft_* 헬퍼)을 실제 자유입력 상품에
적용해 각 옵션의 qty/품절이 소싱 재고대로 계산되는지 검사. set_recommended_options
(PUT)는 호출하지 않음 — 마켓 무변경.

실행(컨테이너): /app/backend/.venv/bin/python3 /tmp/verify_esm_freetext_fallback.py
"""

import asyncio
import json
import traceback

from sqlmodel import select

from backend.db.orm import get_read_session
from backend.domain.samba.account.model import SambaMarketAccount
from backend.domain.samba.collector.model import SambaCollectedProduct
from backend.domain.samba.proxy.esmplus import (
    ESMPlusClient,
    resolve_esm_credentials,
    resolve_esm_master_goods_no,
    _ft_detail_is_freetext,
    _ft_detail_label,
    _ft_norm_label,
    _ft_build_stock_map,
)

# probe 로 확인된 자유입력 샘플 상품
SAMPLES = [
    ("gmarket", "cp_01KPPWHAGFCQESMM7ZZYKWEE09"),
    ("gmarket", "cp_01KPPWHJMW29JRWEB4FJGBGK61"),
    ("auction", "cp_01KPPWHH9VZQ7DY8EYM8729AB4"),
    ("auction", "cp_01KPPWHH93XTKHE44S190T7P5K"),
]


async def verify_one(session, market: str, product_id: str) -> None:
    print(f"\n{'=' * 64}\n[{market}] product={product_id}\n{'=' * 64}", flush=True)
    p = await session.get(SambaCollectedProduct, product_id)
    if not p:
        print("상품 없음", flush=True)
        return
    options = p.options or []
    print(f"소싱 옵션 {len(options)}개", flush=True)

    accts = (
        await session.exec(
            select(SambaMarketAccount).where(
                SambaMarketAccount.market_type == market,
                SambaMarketAccount.is_active == True,  # noqa: E712
            )
        )
    ).all()
    # 이 상품이 등록된 계정 찾기
    acct = None
    goods_no = ""
    for a in accts:
        stored = (p.market_product_nos or {}).get(a.id)
        if isinstance(stored, str) and stored.strip():
            acct, goods_no = a, stored.strip()
            break
        if isinstance(stored, dict):
            for v in stored.values():
                if isinstance(v, (str, int)) and str(v).strip() not in ("", "0"):
                    acct, goods_no = a, str(v).strip()
                    break
        if acct:
            break
    if not acct:
        print("등록 계정/상품번호 못 찾음", flush=True)
        return

    hosting_id, secret_key = await resolve_esm_credentials(session, acct)
    seller_id = (acct.seller_id or "").strip()
    if not seller_id:
        extras = getattr(acct, "additional_fields", None) or {}
        seller_id = (extras.get("apiKey") or extras.get("sellerId") or "").strip()
    client = ESMPlusClient(hosting_id, secret_key, seller_id, site=market)
    site_key_lower = "gmkt" if market == "gmarket" else "iac"
    try:
        master = await resolve_esm_master_goods_no(client, goods_no) or goods_no
        current = await client.get_recommended_options(master)
    finally:
        await client.aclose()

    indep = (
        current.get("independent")
        if isinstance(current.get("independent"), dict)
        else None
    )
    combo = (
        current.get("combination")
        if isinstance(current.get("combination"), dict)
        else None
    )
    all_details = list((indep or {}).get("details") or []) + list(
        (combo or {}).get("details") or []
    )
    ft = sum(1 for d in all_details if _ft_detail_is_freetext(d))
    print(
        f"goods={master} type={current.get('type')} 옵션 {len(all_details)}개 "
        f"(자유입력 {ft}개)",
        flush=True,
    )

    stock_map = _ft_build_stock_map(options)
    print(
        f"소싱 재고맵 {len(stock_map)}건: "
        f"{json.dumps(dict(list(stock_map.items())[:6]), ensure_ascii=False)}",
        flush=True,
    )

    matched = unmatched = 0
    rows = []
    for d in all_details:
        label = _ft_detail_label(d)
        info = stock_map.get(_ft_norm_label(label))
        cur_qty = (d.get("qty") or {}).get(site_key_lower)
        cur_sold = d.get("isSoldOut")
        if info is not None:
            new_qty, new_sold = info
            matched += 1
        else:
            new_qty, new_sold = 0, True
            unmatched += 1
        rows.append(
            f"  '{label}': qty {cur_qty}->{new_qty}  sold {cur_sold}->{new_sold}"
            f"  {'(소싱없음→품절)' if info is None else ''}"
        )
    print(f"매칭 {matched} / 미매칭 {unmatched}", flush=True)
    print("\n".join(rows), flush=True)
    # PUT 미실행 — 검증 전용
    print(
        ">>> PUT 생략(dry-run). payload type 보존 + qty/품절만 교체 검증 완료.",
        flush=True,
    )


async def main() -> None:
    async with get_read_session() as session:
        for market, pid in SAMPLES:
            try:
                await verify_one(session, market, pid)
            except Exception:
                print(f"[{market}/{pid}] 예외:\n{traceback.format_exc()}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
