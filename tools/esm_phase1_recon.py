# -*- coding: utf-8 -*-
"""ESM(G마켓/옥션) Phase 1 인증/읽기 점검 + 등록 후보 정찰 스크립트.

읽기 전용. register/update/delete 호출 없음.
컨테이너 내 실행: /app/backend/.venv/bin/python3 /tmp/esm_phase1_recon.py
"""
import asyncio
import datetime as dt
import json
import traceback
from typing import Any

from sqlmodel import select

from backend.db.orm import get_read_session
from backend.domain.samba.account.model import SambaMarketAccount
from backend.domain.samba.proxy.esmplus import ESMPlusClient, resolve_esm_credentials


def _mask(s: str) -> str:
    if not s:
        return "(빈값)"
    if len(s) <= 6:
        return s[0] + "***"
    return s[:3] + "***" + s[-2:]


async def _build_creds(account: Any) -> dict:
    """market_base._load_auth 흐름 모사 (account 단독)."""
    extras = account.additional_fields or {}
    creds = {k: v for k, v in extras.items() if v}
    if not creds:
        if account.api_key:
            creds["apiKey"] = account.api_key
        if account.api_secret:
            creds["apiSecret"] = account.api_secret
    return creds


async def check_account(session, account, site: str) -> dict:
    out: dict[str, Any] = {
        "account_id": account.id,
        "market_type": account.market_type,
        "market_name": account.market_name,
        "account_label": account.account_label,
        "is_active": account.is_active,
        "calls": {},
    }
    creds = await _build_creds(account)
    seller_id = (
        creds.get("apiKey", "")
        or creds.get("sellerId", "")
        or (account.seller_id or "")
    )
    out["seller_id"] = _mask(seller_id)
    out["seller_id_present"] = bool(seller_id)

    hosting_id, secret_key = await resolve_esm_credentials(session, account)
    out["hosting_id"] = _mask(hosting_id)
    out["secret_key_present"] = bool(secret_key)

    if not (hosting_id and secret_key and seller_id):
        out["calls"]["_precondition"] = (
            f"FAIL — hosting_id={bool(hosting_id)} secret={bool(secret_key)} seller_id={bool(seller_id)}"
        )
        return out

    client = ESMPlusClient(hosting_id, secret_key, seller_id, site=site)
    try:
        # 1) get_categories — 전체 대분류
        try:
            cats = await client.get_categories()
            site_cats = cats.get("siteCats") or cats.get("data") or cats
            n = len(site_cats) if isinstance(site_cats, list) else "dict"
            out["calls"]["get_categories"] = f"OK (대분류 {n}건)"
        except Exception as e:
            out["calls"]["get_categories"] = f"FAIL — {type(e).__name__}: {e}"

        # 2) get_places
        try:
            places = await client.get_places()
            out["calls"]["get_places"] = f"OK ({len(places)}건)"
            out["places_sample"] = places[:2]
        except Exception as e:
            out["calls"]["get_places"] = f"FAIL — {type(e).__name__}: {e}"

        # 3) get_dispatch_policies
        try:
            pol = await client.get_dispatch_policies()
            out["calls"]["get_dispatch_policies"] = f"OK ({len(pol)}건)"
            out["dispatch_sample"] = pol[:2]
        except Exception as e:
            out["calls"]["get_dispatch_policies"] = f"FAIL — {type(e).__name__}: {e}"

        # 4) search_products — 최소 파라미터(실제 응답으로 스펙 학습)
        for params in (
            {"pageIndex": 1, "pageSize": 5},
            {"page": 1, "size": 5},
            {},
        ):
            try:
                sp = await client.search_products(params)
                keys = list(sp.keys()) if isinstance(sp, dict) else type(sp).__name__
                data = sp.get("data") or sp.get("goods") or sp.get("list") or []
                cnt = len(data) if isinstance(data, list) else "?"
                out["calls"]["search_products"] = (
                    f"OK params={params} keys={keys} 상품={cnt}건"
                )
                # 등록된 상품 이미지 URL 패턴 1건 노출(있으면)
                if isinstance(data, list) and data:
                    out["existing_product_sample"] = json.dumps(
                        data[0], ensure_ascii=False
                    )[:800]
                break
            except Exception as e:
                out["calls"]["search_products"] = f"FAIL params={params} — {type(e).__name__}: {e}"

        # 5) search_orders — 최근 3일
        today = dt.date.today()
        d_from = (today - dt.timedelta(days=3)).strftime("%Y-%m-%d")
        d_to = today.strftime("%Y-%m-%d")
        site_type = 2 if site == "gmarket" else 1
        order_params = {
            "siteType": site_type,
            "orderStatus": 1,  # 결제완료
            "requestDateType": 2,  # 결제일
            "requestDateFrom": d_from,
            "requestDateTo": d_to,
            "pageIndex": 1,
            "pageSize": 5,
        }
        try:
            so = await client.search_orders(order_params)
            keys = list(so.keys()) if isinstance(so, dict) else type(so).__name__
            out["calls"]["search_orders"] = f"OK ({d_from}~{d_to}) keys={keys}"
        except Exception as e:
            out["calls"]["search_orders"] = f"FAIL ({d_from}~{d_to}) — {type(e).__name__}: {e}"
    finally:
        await client.aclose()
    return out


async def find_candidate_products(session) -> dict:
    """무신사 등록 후보 — 브랜드/카테고리 상이 3건 + 이미지 URL 패턴 점검."""
    from backend.domain.samba.collector.model import SambaCollectedProduct as CP

    out: dict[str, Any] = {"musinsa_sample": []}
    try:
        stmt = (
            select(
                CP.id,
                CP.name,
                CP.brand,
                CP.category,
                CP.sale_price,
                CP.images,
                CP.source_site,
            )
            .where(CP.source_site == "MUSINSA")
            .limit(40)
        )
        rows = (await session.execute(stmt)).all()
        seen_brand: set = set()
        seen_cat: set = set()
        for r in rows:
            imgs = r.images or []
            first = imgs[0] if imgs else ""
            cdn_kind = "msscdn(원본)" if "msscdn" in str(first) else (
                "samba-proxy" if "samba-wave" in str(first) else (
                    "기타:" + str(first)[:40] if first else "이미지없음"
                )
            )
            brand = (r.brand or "").strip()
            cat = (r.category or "").strip()
            out["musinsa_sample"].append(
                {
                    "id": r.id,
                    "brand": brand,
                    "category": cat,
                    "sale_price": r.sale_price,
                    "img_count": len(imgs),
                    "img0_kind": cdn_kind,
                    "img0": str(first)[:90],
                }
            )
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}\n{traceback.format_exc()[:600]}"
    return out


async def main():
    report: dict[str, Any] = {"accounts": [], "candidates": {}}
    async with get_read_session() as session:
        for mtype, site in (("gmarket", "gmarket"), ("auction", "auction")):
            stmt = select(SambaMarketAccount).where(
                SambaMarketAccount.market_type == mtype,
                SambaMarketAccount.is_active == True,  # noqa: E712
            )
            accts = (await session.execute(stmt)).scalars().all()
            if not accts:
                report["accounts"].append({"market_type": mtype, "error": "활성 계정 없음"})
                continue
            for acc in accts:
                try:
                    report["accounts"].append(await check_account(session, acc, site))
                except Exception as e:
                    report["accounts"].append(
                        {
                            "account_id": acc.id,
                            "market_type": mtype,
                            "fatal": f"{type(e).__name__}: {e}\n{traceback.format_exc()[:800]}",
                        }
                    )
        # 계정 결과 먼저 출력(candidate 실패해도 보존)
        print("=" * 60)
        print("ESM PHASE1 — ACCOUNTS")
        print("=" * 60)
        print(json.dumps(report["accounts"], ensure_ascii=False, indent=2, default=str))
        try:
            report["candidates"] = await find_candidate_products(session)
        except Exception as e:
            report["candidates"] = {"fatal": f"{type(e).__name__}: {e}"}

    print("=" * 60)
    print("ESM PHASE1 — CANDIDATES (무신사)")
    print("=" * 60)
    print(json.dumps(report["candidates"], ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
