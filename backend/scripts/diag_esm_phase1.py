"""ESM(G마켓/옥션) Phase 1 점검 — 인증/읽기. 무변경.

플러그인(gmarket.py/auction.py)과 동일한 client 구성:
- seller_id = account.api_key | account.seller_id | additional_fields.apiKey/sellerId
- hosting_id/secret_key = resolve_esm_credentials(session, account)
"""

import asyncio
import json
from datetime import datetime, timedelta


async def main() -> None:
    from sqlmodel import select

    from backend.db.orm import get_write_session
    from backend.domain.samba.account.model import SambaMarketAccount
    from backend.domain.samba.proxy.esmplus import (
        ESMPlusClient,
        resolve_esm_credentials,
    )

    report: dict = {}
    async with get_write_session() as session:
        for site, mt, site_type in [
            ("gmarket", "gmarket", 2),
            ("auction", "auction", 1),
        ]:
            r: dict = {}
            rows = (
                (
                    await session.execute(
                        select(SambaMarketAccount)
                        .where(SambaMarketAccount.market_type == mt)
                        .limit(3)
                    )
                )
                .scalars()
                .all()
            )
            if not rows:
                report[site] = {"error": f"{mt} 계정 없음"}
                continue
            acc = rows[0]
            extras = getattr(acc, "additional_fields", None) or {}
            seller_id = (
                (getattr(acc, "api_key", "") or "")
                or (getattr(acc, "seller_id", "") or "")
                or (extras.get("apiKey") or extras.get("sellerId") or "")
            )
            hosting_id, secret_key = await resolve_esm_credentials(session, acc)
            r["account"] = {
                "id": acc.id,
                "label": acc.account_label,
                "market_name": acc.market_name,
                "seller_id_present": bool(seller_id),
                "seller_id_tail": seller_id[-4:] if seller_id else "",
                "hosting_id": hosting_id,
                "secret_present": bool(secret_key),
                "addl_keys": list(extras.keys()),
                "total_accounts": len(rows),
            }
            if not (hosting_id and secret_key and seller_id):
                r["FATAL"] = "인증정보 누락 (hosting/secret/seller 중 빈 값)"
                report[site] = r
                continue

            client = ESMPlusClient(hosting_id, secret_key, seller_id, site=site)
            try:
                # 1) 카테고리
                try:
                    cats = await client.get_categories()
                    r["get_categories"] = {
                        "ok": True,
                        "keys": list(cats.keys())[:8]
                        if isinstance(cats, dict)
                        else None,
                        "sample": str(cats)[:300],
                    }
                except Exception as e:
                    r["get_categories"] = {
                        "ok": False,
                        "err": f"{type(e).__name__}: {e}",
                    }

                # 2) 출고지
                try:
                    pl = await client.get_places()
                    r["get_places"] = {
                        "ok": True,
                        "count": len(pl),
                        "sample": str(pl[0])[:250] if pl else None,
                    }
                except Exception as e:
                    r["get_places"] = {"ok": False, "err": f"{type(e).__name__}: {e}"}

                # 3) 발송정책
                try:
                    dp = await client.get_dispatch_policies()
                    r["get_dispatch_policies"] = {
                        "ok": True,
                        "count": len(dp),
                        "sample": str(dp[0])[:250] if dp else None,
                    }
                except Exception as e:
                    r["get_dispatch_policies"] = {
                        "ok": False,
                        "err": f"{type(e).__name__}: {e}",
                    }

                # 4) 상품 조회 (소량)
                try:
                    sp = await client.search_products({"pageIndex": 1, "pageSize": 5})
                    r["search_products"] = {
                        "ok": True,
                        "keys": list(sp.keys())[:10] if isinstance(sp, dict) else None,
                        "sample": str(sp)[:400],
                    }
                except Exception as e:
                    r["search_products"] = {
                        "ok": False,
                        "err": f"{type(e).__name__}: {e}",
                    }

                # 5) 주문 조회 (최근 3일, 결제완료)
                try:
                    today = datetime.now()
                    frm = (today - timedelta(days=3)).strftime("%Y-%m-%d")
                    to = today.strftime("%Y-%m-%d")
                    so = await client.search_orders(
                        {
                            "siteType": site_type,
                            "orderStatus": 1,
                            "requestDateType": 2,
                            "requestDateFrom": frm,
                            "requestDateTo": to,
                            "pageIndex": 1,
                            "pageSize": 5,
                        }
                    )
                    r["search_orders"] = {
                        "ok": True,
                        "keys": list(so.keys())[:10] if isinstance(so, dict) else None,
                        "sample": str(so)[:400],
                    }
                except Exception as e:
                    r["search_orders"] = {
                        "ok": False,
                        "err": f"{type(e).__name__}: {e}",
                    }
            finally:
                await client.aclose()
            report[site] = r

    print("PHASE1_REPORT=" + json.dumps(report, ensure_ascii=False, default=str))


asyncio.run(main())
