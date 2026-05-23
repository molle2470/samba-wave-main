# -*- coding: utf-8 -*-
"""ESM 고시정보 그룹 코드 조회 + 상품 그룹 감지. 읽기 전용.
group 35(기타) 및 양말 관련 그룹의 필수 itemelement 코드 확인."""
import asyncio
import json
import sys

from backend.db.orm import get_read_session
from backend.domain.samba.account.repository import SambaMarketAccountRepository
from backend.domain.samba.collector.repository import SambaCollectedProductRepository
from backend.domain.samba.proxy.esmplus import ESMPlusClient, resolve_esm_credentials
from backend.domain.samba.proxy.notice_utils import detect_notice_group

GMARKET_ID = "ma_01KN1DEKNZYW38RDPFVW7VNYCY"
PRODUCT_ID = sys.argv[1] if len(sys.argv) > 1 else "cp_01KMXZ8NJ1Q4CE2DH67PAG7Q7Q"
GROUPS = [int(x) for x in (sys.argv[2].split(",") if len(sys.argv) > 2 else ["35", "1", "4"])]


async def main():
    out = {}
    async with get_read_session() as session:
        # 상품 그룹 감지
        prepo = SambaCollectedProductRepository(session)
        p = await prepo.get_async(PRODUCT_ID)
        pdict = p.model_dump() if p else {}
        out["product"] = {
            "id": PRODUCT_ID,
            "category": pdict.get("category"),
            "detected_group": detect_notice_group(pdict),
        }
        repo = SambaMarketAccountRepository(session)
        a = await repo.get_async(GMARKET_ID)
        extras = a.additional_fields or {}
        creds = {k: v for k, v in extras.items() if v}
        seller_id = creds.get("apiKey", "") or creds.get("sellerId", "") or (a.seller_id or "")
        hosting_id, secret_key = await resolve_esm_credentials(session, a)
    client = ESMPlusClient(hosting_id, secret_key, seller_id, site="gmarket")
    out["groups"] = {}
    try:
        # 전체 그룹 목록
        try:
            g = await client._call_api("GET", "/item/v1/official-notice/groups")
            grp_list = g.get("officialNoticeGroups") or g.get("data") or g
            out["all_groups"] = grp_list
        except Exception as e:
            out["all_groups_err"] = str(e)[:150]
        for no in GROUPS:
            try:
                c = await client._call_api(
                    "GET", f"/item/v1/official-notice/groups/{no}/codes"
                )
                codes = c.get("codes") or c.get("data") or c
                # 필수(isExtraMark) 항목만 추려서
                req = []
                if isinstance(codes, list):
                    for it in codes:
                        req.append(
                            {
                                "code": it.get("officialNoticeItemelementCode")
                                or it.get("itemelementCode")
                                or it.get("code"),
                                "name": it.get("itemelementName") or it.get("name"),
                                "isExtraMark": it.get("isExtraMark"),
                            }
                        )
                out["groups"][no] = req or codes
            except Exception as e:
                out["groups"][no] = {"err": str(e)[:150]}
    finally:
        await client.aclose()
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
