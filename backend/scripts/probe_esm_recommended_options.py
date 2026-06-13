# -*- coding: utf-8 -*-
"""#413 진단 — ESM(지마켓/옥션) GET /recommended-options 응답 구조 캡처.

목적: 자유입력(recommendedOptValueNo=0) 옵션이 GET 응답에서 어떤 형태로
오는지 실물 확인. fallback(update_existing_freetext_stock) 작성 근거 확보.

읽기 전용 — DB 조회 + ESM GET 호출만. 등록/수정 PUT 없음.

실행(컨테이너): /app/backend/.venv/bin/python3 /tmp/probe_esm_recommended_options.py
"""

import asyncio
import json
import sys
import traceback

from sqlmodel import select

from backend.db.orm import get_read_session
from backend.domain.samba.account.model import SambaMarketAccount
from backend.domain.samba.collector.model import SambaCollectedProduct
from backend.domain.samba.proxy.esmplus import (
    ESMPlusClient,
    resolve_esm_credentials,
    resolve_esm_master_goods_no,
)

MARKETS = ["gmarket", "auction"]
MAX_PRODUCTS_PER_MARKET = 30  # GET 호출 상한(레이트리밋 보호)
FREETEXT_SAMPLES_PER_MARKET = 2  # 자유입력 샘플 충분히 모으면 조기 중단


def _extract_goods_no(val) -> str:
    """market_product_nos[account_id] 값 → goods_no 문자열 추출 (str/dict 모두 대응)."""
    if isinstance(val, str):
        return val.strip()
    if isinstance(val, dict):
        for k in ("goodsNo", "originProductNo", "productNo", "id"):
            v = val.get(k)
            if isinstance(v, (str, int)) and str(v).strip() not in ("", "0"):
                return str(v).strip()
        # 마지막 폴백 — 첫 비어있지 않은 스칼라 값
        for v in val.values():
            if isinstance(v, (str, int)) and str(v).strip() not in ("", "0"):
                return str(v).strip()
    return ""


def _is_freetext_detail(d: dict) -> bool:
    """detail 이 자유입력 옵션값인지 — recommendedOptValueNo(1/2) == 0."""
    if not isinstance(d, dict):
        return False
    for key in (
        "recommendedOptValueNo",
        "recommendedOptValueNo1",
        "recommendedOptValueNo2",
    ):
        if key in d and (d.get(key) == 0 or d.get(key) == "0"):
            return True
    return False


def _scan_freetext(resp: dict) -> bool:
    """응답 어딘가에 자유입력 detail 이 있는지 재귀 스캔."""
    found = [False]

    def walk(o):
        if found[0]:
            return
        if isinstance(o, dict):
            if _is_freetext_detail(o):
                found[0] = True
                return
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(resp)
    return found[0]


async def probe_market(session, market: str) -> None:
    print(f"\n{'=' * 70}\n[{market}] 시작\n{'=' * 70}", flush=True)

    accts = (
        await session.exec(
            select(SambaMarketAccount).where(
                SambaMarketAccount.market_type == market,
                SambaMarketAccount.is_active == True,  # noqa: E712
            )
        )
    ).all()
    if not accts:
        print(f"[{market}] 활성 계정 없음 — 스킵", flush=True)
        return
    print(f"[{market}] 활성 계정 {len(accts)}개", flush=True)

    freetext_found = 0
    normal_printed = 0

    for acct in accts:
        if freetext_found >= FREETEXT_SAMPLES_PER_MARKET:
            break
        hosting_id, secret_key = await resolve_esm_credentials(session, acct)
        seller_id = (acct.seller_id or "").strip()
        if not seller_id:
            extras = getattr(acct, "additional_fields", None) or {}
            seller_id = (extras.get("apiKey") or extras.get("sellerId") or "").strip()
        if not (hosting_id and secret_key and seller_id):
            print(
                f"[{market}] 계정 {acct.id} 인증정보 부족 "
                f"(hosting={bool(hosting_id)} secret={bool(secret_key)} seller={bool(seller_id)}) — 스킵",
                flush=True,
            )
            continue

        # 이 계정으로 등록된 + 옵션 있는 상품 조회
        prods = (
            await session.exec(
                select(SambaCollectedProduct)
                .where(
                    SambaCollectedProduct.market_product_nos.op("?")(acct.id),  # type: ignore[attr-defined]
                    SambaCollectedProduct.options != None,  # noqa: E711
                )
                .limit(MAX_PRODUCTS_PER_MARKET)
            )
        ).all()
        print(
            f"[{market}] 계정 {acct.account_label or acct.id}: 후보 상품 {len(prods)}개",
            flush=True,
        )
        if not prods:
            continue

        client = ESMPlusClient(hosting_id, secret_key, seller_id, site=market)
        try:
            for p in prods:
                if freetext_found >= FREETEXT_SAMPLES_PER_MARKET:
                    break
                opts = p.options or []
                if not opts:
                    continue
                stored = (p.market_product_nos or {}).get(acct.id)
                goods_no = _extract_goods_no(stored)
                if not goods_no:
                    continue
                try:
                    master = (
                        await resolve_esm_master_goods_no(client, goods_no) or goods_no
                    )
                    resp = await client.get_recommended_options(master)
                except Exception as ge:
                    print(
                        f"   - goods={goods_no} GET 실패: {str(ge)[:120]}",
                        flush=True,
                    )
                    continue

                is_ft = _scan_freetext(resp)
                if is_ft and freetext_found < FREETEXT_SAMPLES_PER_MARKET:
                    freetext_found += 1
                    print(
                        f"\n>>> [자유입력 샘플 #{freetext_found}] market={market} "
                        f"product={p.id} name={(p.name or '')[:40]!r} goods={master}\n"
                        f"    samba options={json.dumps(opts, ensure_ascii=False)[:300]}",
                        flush=True,
                    )
                    print(json.dumps(resp, ensure_ascii=False, indent=2), flush=True)
                elif not is_ft and normal_printed < 1:
                    normal_printed += 1
                    print(
                        f"\n--- [추천옵션(정상) 참고샘플] market={market} goods={master} ---",
                        flush=True,
                    )
                    print(
                        json.dumps(resp, ensure_ascii=False, indent=2)[:1500],
                        flush=True,
                    )
        finally:
            await client.aclose()

    print(
        f"\n[{market}] 종료 — 자유입력 샘플 {freetext_found}개 캡처",
        flush=True,
    )


async def main() -> None:
    async with get_read_session() as session:
        for market in MARKETS:
            try:
                await probe_market(session, market)
            except Exception:
                print(f"[{market}] 예외:\n{traceback.format_exc()}", flush=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
