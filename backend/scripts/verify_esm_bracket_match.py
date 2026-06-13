# -*- coding: utf-8 -*-
"""#416 검증 — 괄호코드 옵션값 매칭(괄호 제거 재시도) 실측.

cat 300027334 의 ESM 색상/사이즈 추천옵션 풀을 받아, 괄호 붙은 옵션값이
match_option_value 로 매칭되는지 확인(읽기 전용, PUT 없음).

실행(컨테이너): /app/backend/.venv/bin/python3 /tmp/vb.py
"""

import asyncio
import traceback

from sqlmodel import select

from backend.db.orm import get_read_session
from backend.domain.samba.account.model import SambaMarketAccount
from backend.domain.samba.proxy.esmplus import (
    ESMPlusClient,
    resolve_esm_credentials,
    _resolve_esm_group,
)

CAT = "300027334"
COLOR_SAMPLES = [
    "살몬(018)",
    "그린(GN)",
    "화이트(WH)",
    "아이보리(IV)",
    "퍼플(PPL)",
    "네이비(NVY)",
    "바이올렛(VIO)",
    "카키(054)",
    "블랙",
    "O화이트(011)",
]
SIZE_SAMPLES = ["2XL(80)", "L(70)", "80(XS)", "M(90)", "100", "2T(90)"]


def _name_map(pool):
    out = {}
    for v in pool:
        no = v.get("recommendedOptValueNo")
        nm = (v.get("recommendedOptValueName") or {}).get("kor")
        if no:
            out[int(no)] = nm
    return out


async def run(session) -> None:
    accts = (
        await session.exec(
            select(SambaMarketAccount).where(
                SambaMarketAccount.market_type == "gmarket",
                SambaMarketAccount.is_active == True,  # noqa: E712
            )
        )
    ).all()
    client = None
    for a in accts:
        hosting_id, secret_key = await resolve_esm_credentials(session, a)
        seller_id = (a.seller_id or "").strip()
        if not seller_id:
            extras = getattr(a, "additional_fields", None) or {}
            seller_id = (extras.get("apiKey") or extras.get("sellerId") or "").strip()
        if hosting_id and secret_key and seller_id:
            client = ESMPlusClient(hosting_id, secret_key, seller_id, site="gmarket")
            print(f"계정: {a.account_label} (seller={seller_id})", flush=True)
            break
    if not client:
        print("사용 가능 gmarket 계정 없음", flush=True)
        return

    try:
        cg, cpool = await _resolve_esm_group(client, CAT, "색상")
        sg, spool = await _resolve_esm_group(client, CAT, "사이즈")
        print(f"색상 풀 {len(cpool)}건 / 사이즈 풀 {len(spool)}건\n", flush=True)
        cmap, smap = _name_map(cpool), _name_map(spool)

        print("=== 색상 ===", flush=True)
        ok = 0
        for s in COLOR_SAMPLES:
            no = ESMPlusClient.match_option_value(s, cpool)
            label = cmap.get(no) if no else None
            if no:
                ok += 1
            print(
                f"  {s:14s} -> {('%s(%s)' % (label, no)) if no else 'None'}", flush=True
            )
        print(f"  색상 매칭 {ok}/{len(COLOR_SAMPLES)}\n", flush=True)

        print("=== 사이즈 ===", flush=True)
        ok = 0
        for s in SIZE_SAMPLES:
            no = ESMPlusClient.match_option_value(s, spool)
            label = smap.get(no) if no else None
            if no:
                ok += 1
            print(
                f"  {s:14s} -> {('%s(%s)' % (label, no)) if no else 'None'}", flush=True
            )
        print(f"  사이즈 매칭 {ok}/{len(SIZE_SAMPLES)}", flush=True)
    finally:
        await client.aclose()


async def main() -> None:
    async with get_read_session() as session:
        try:
            await run(session)
        except Exception:
            print(traceback.format_exc(), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
