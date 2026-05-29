"""dry-run: 쿠팡 modelNo 일괄 보강 대상 카운트 + 샘플 1건 응답 구조 확인.

목적
----
- 쿠팡 등록 상품 중 modelNo 비어있는 옵션 수 파악
- 쿠팡 GET response 의 items[].modelNo 위치 확인 + 그대로 PUT 가능한지 검토
- 실제 PUT 호출 안 함 (read-only)
"""

import asyncio
import json
from typing import Any

import asyncpg

from backend.core.config import settings
from backend.domain.samba.proxy.coupang import CoupangClient


async def fetch_targets(conn: asyncpg.Connection) -> list[dict[str, Any]]:
    """쿠팡 계정 + style_code 있는 등록상품 list."""
    rows = await conn.fetch(
        """
        SELECT
          cp.id              AS pid,
          cp.style_code      AS style_code,
          cp.market_product_nos AS mpn,
          cp.registered_accounts AS regs,
          cp.name            AS name
        FROM samba_collected_product cp
        WHERE cp.status = 'registered'
          AND cp.style_code IS NOT NULL
          AND BTRIM(cp.style_code) <> ''
          AND cp.market_product_nos::text LIKE '%ma_%'
        """
    )

    targets: list[dict[str, Any]] = []
    for r in rows:
        mpn_raw = r["mpn"]
        mpn = json.loads(mpn_raw) if isinstance(mpn_raw, str) else (mpn_raw or {})
        regs_raw = r["regs"]
        regs = json.loads(regs_raw) if isinstance(regs_raw, str) else (regs_raw or [])

        for acc_id in regs:
            if not isinstance(acc_id, str) or not acc_id.startswith("ma_"):
                continue
            spid = mpn.get(acc_id)
            if not spid or not isinstance(spid, str) or not spid.isdigit():
                continue
            targets.append(
                {
                    "pid": r["pid"],
                    "name": r["name"],
                    "style_code": r["style_code"],
                    "account_id": acc_id,
                    "spid": spid,
                }
            )
    return targets


async def filter_coupang_accounts(
    conn: asyncpg.Connection, targets: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, dict[str, str]]]:
    """쿠팡 계정만 필터 + accountId → creds 매핑."""
    if not targets:
        return [], {}
    acc_ids = sorted({t["account_id"] for t in targets})
    rows = await conn.fetch(
        """
        SELECT id, market_type, api_key, api_secret, seller_id, account_label,
               additional_fields
        FROM samba_market_account
        WHERE id = ANY($1::text[]) AND market_type = 'coupang'
        """,
        acc_ids,
    )
    creds_map: dict[str, dict[str, str]] = {}
    for r in rows:
        af_raw = r["additional_fields"]
        af = (
            json.loads(af_raw)
            if isinstance(af_raw, str)
            else (af_raw or {})
            if isinstance(af_raw, dict)
            else {}
        )
        ak = (r["api_key"] or "").strip() or str(af.get("accessKey") or "").strip()
        sk = (r["api_secret"] or "").strip() or str(af.get("secretKey") or "").strip()
        vid = (r["seller_id"] or "").strip() or str(af.get("vendorId") or "").strip()
        print(
            f"         [creds] {r['id']} ({r['account_label']}): "
            f"ak_len={len(ak)} sk_len={len(sk)} vid={vid!r} "
            f"af_keys={list(af.keys()) if isinstance(af, dict) else 'NA'}"
        )
        creds_map[r["id"]] = {
            "access_key": ak,
            "secret_key": sk,
            "vendor_id": vid,
            "label": r["account_label"] or "",
        }
    coupang_targets = [t for t in targets if t["account_id"] in creds_map]
    return coupang_targets, creds_map


async def sample_get(
    creds: dict[str, str], spid: str
) -> tuple[bool, list[str], dict[str, Any] | None]:
    """1건 쿠팡 GET → items modelNo 상태 + raw response 반환."""
    try:
        client = CoupangClient(
            access_key=creds["access_key"],
            secret_key=creds["secret_key"],
            vendor_id=creds["vendor_id"],
        )
        resp = await client.get_product(spid)
        data = resp.get("data") if isinstance(resp, dict) else resp
        if isinstance(data, dict) and "data" in data:
            data = data["data"]
        items = data.get("items", []) if isinstance(data, dict) else []
        model_nos: list[str] = []
        for it in items:
            if isinstance(it, dict):
                model_nos.append(str(it.get("modelNo") or ""))
        return True, model_nos, data
    except Exception as e:
        print(f"  [GET 실패] spid={spid}: {type(e).__name__}: {str(e)[:200]}")
        return False, [], None


async def main() -> None:
    conn = await asyncpg.connect(
        host="172.18.0.2",
        port=5432,
        user=settings.write_db_user,
        password=settings.write_db_password,
        database=settings.write_db_name,
        ssl=False,
    )

    try:
        targets = await fetch_targets(conn)
        print(f"[STEP 1] DB 매칭 rows (status=registered + style_code 有): {len(targets):,}")

        coupang_targets, creds_map = await filter_coupang_accounts(conn, targets)
        print(f"[STEP 2] 쿠팡 계정 한정 spid 수: {len(coupang_targets):,}")
        print(f"         쿠팡 계정 수: {len(creds_map)}")
        for acc_id, c in creds_map.items():
            cnt = sum(1 for t in coupang_targets if t["account_id"] == acc_id)
            print(f"         - {acc_id} ({c['label']}): {cnt:,}건")

        if not coupang_targets:
            print("\n[종료] 대상 없음.")
            return

        sample = coupang_targets[0]
        print(f"\n[STEP 3] 샘플 1건 GET — pid={sample['pid']}, spid={sample['spid']}")
        print(f"         style_code (DB): {sample['style_code']}")
        creds = creds_map[sample["account_id"]]

        ok, model_nos, data = await sample_get(creds, sample["spid"])
        if not ok or data is None:
            print("[샘플 GET 실패]")
            return

        print(f"         items 수: {len(model_nos)}")
        print(f"         items[].modelNo 현재 값: {model_nos}")
        empty_cnt = sum(1 for m in model_nos if not m.strip())
        print(f"         빈 modelNo 수: {empty_cnt}/{len(model_nos)}")

        print("\n[STEP 4] GET response top-level 키:")
        if isinstance(data, dict):
            for k in list(data.keys())[:30]:
                v = data[k]
                tn = type(v).__name__
                preview = str(v)[:60] if not isinstance(v, (list, dict)) else f"({tn}, len={len(v) if hasattr(v, '__len__') else '?'})"
                print(f"  - {k}: {preview}")

        print("\n[STEP 5] items[0] 키 (PUT body 호환성):")
        items_raw = data.get("items", []) if isinstance(data, dict) else []
        if items_raw and isinstance(items_raw[0], dict):
            for k in list(items_raw[0].keys())[:40]:
                v = items_raw[0][k]
                preview = str(v)[:60] if not isinstance(v, (list, dict)) else f"({type(v).__name__})"
                print(f"  - {k}: {preview}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
