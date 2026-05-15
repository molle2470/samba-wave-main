"""SSG 상품 원본 페이지 fetch로 breadcrumb 보정 (v2).

backfill_ssg_category.py 가 처리하지 못한 잔여 상품(같은 brand의 풀 path
필터가 없어 매칭 실패한 상품들)을 위해, 각 상품의 source_url 에서 SSG 페이지
HTML을 직접 fetch해 breadcrumb DOM을 파싱한다. 추출한 풀 path 로:

1) 같은 brand 의 풀 path 필터가 있으면 매칭 (백필 v1 매칭 재시도)
2) 없으면 신규 풀 path 필터를 자동 생성 (`SSG_{brand}_{대}_{중}_{소}`)
3) product.search_filter_id / category / category1~4 UPDATE
4) 사용처가 0건이 된 leaf-only 필터 삭제

VM 컨테이너 내부 실행 (한국 IP + 컨테이너 환경 일관성):
    docker cp scripts/backfill_ssg_category_v2.py samba-samba-api-1:/tmp/
    docker exec samba-samba-api-1 /app/backend/.venv/bin/python \\
        /tmp/backfill_ssg_category_v2.py --dry-run
    docker exec samba-samba-api-1 /app/backend/.venv/bin/python \\
        /tmp/backfill_ssg_category_v2.py --apply
"""

from __future__ import annotations

import argparse
import asyncio
import re
from typing import Optional

import asyncpg
import httpx
from ulid import ULID

from backend.core.config import settings


SQL_FETCH_FILTERS = """
SELECT id, name, source_brand_name, parent_id, tenant_id, keyword
FROM samba_search_filter
WHERE source_site = 'SSG' AND name IS NOT NULL
"""

SQL_FETCH_BAD_PRODUCTS = """
SELECT id, search_filter_id, brand, source_url, site_product_id, tenant_id
FROM samba_collected_product
WHERE source_site = 'SSG'
  AND (category IS NULL OR category = '' OR category NOT LIKE '%>%')
"""

SQL_UPDATE_PRODUCT = """
UPDATE samba_collected_product
SET search_filter_id = $1,
    category = $2,
    category1 = $3,
    category2 = $4,
    category3 = $5,
    category4 = $6
WHERE id = $7
"""

SQL_INSERT_FILTER = """
INSERT INTO samba_search_filter
  (id, source_site, name, parent_id, tenant_id, keyword,
   source_brand_name, requested_count, created_at, updated_at)
VALUES
  ($1, 'SSG', $2, $3, $4, $5, $6, 0, NOW(), NOW())
"""

SQL_COUNT_USAGE = """
SELECT search_filter_id, COUNT(*) AS n
FROM samba_collected_product
WHERE search_filter_id = ANY($1::text[])
GROUP BY search_filter_id
"""

SQL_DELETE_FILTERS = """
DELETE FROM samba_search_filter WHERE id = ANY($1::text[])
"""


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9",
}


_TAREA_RE = re.compile(
    r'data-react-tarea="[^"]*카테고리 로케이션\|[^"]*카테고리"[^>]*>\s*([^<]+?)\s*</a>'
)


def _parse_filter_name(name: str) -> tuple[str, list[str]]:
    parts = [p for p in name.split("_") if p]
    if len(parts) < 2 or parts[0] != "SSG":
        return ("", [])
    return (parts[1], parts[2:])


async def _connect() -> asyncpg.Connection:
    return await asyncpg.connect(
        user=settings.write_db_user,
        password=settings.write_db_password,
        host=settings.write_db_host,
        port=settings.write_db_port,
        database=settings.write_db_name,
        ssl=False,
    )


async def _fetch_breadcrumb(client: httpx.AsyncClient, url: str) -> list[str]:
    """SSG 상품 페이지 HTML → breadcrumb 리스트.

    `data-react-tarea` 카테고리 로케이션 셀렉터를 정규식으로 파싱.
    실패 시 빈 리스트.
    """
    if not url:
        return []
    try:
        r = await client.get(url, headers=HEADERS, timeout=15.0)
        if r.status_code != 200:
            return []
        html = r.text
    except Exception:
        return []

    found = _TAREA_RE.findall(html)
    bc: list[str] = []
    seen: set[str] = set()
    for raw in found:
        t = raw.strip()
        if not t or t in seen:
            continue
        if t in ("신세계백화점", "SSG", "SSG.COM"):
            continue
        seen.add(t)
        bc.append(t)
    # 보조: department 페이지용 li 패턴
    if len(bc) < 2:
        for m in re.finditer(
            r"<a[^>]*class=\"[^\"]*cdtl_loca[^\"]*\"[^>]*>\s*([^<]+?)\s*</a>",
            html,
        ):
            t = m.group(1).strip()
            if t and t not in seen and t not in ("신세계백화점", "SSG"):
                seen.add(t)
                bc.append(t)
    return bc[:4]


def _gen_filter_id() -> str:
    return f"sf_{ULID()}"


async def run(dry_run: bool) -> None:
    conn = await _connect()
    try:
        # 1) 필터 인덱스 — same brand 의 풀 path 필터 lookup
        rows = await conn.fetch(SQL_FETCH_FILTERS)
        # full_map[(brand, " > ".join(path))] = filter row
        full_map: dict[tuple[str, str], dict] = {}
        # leaf_full_map[(brand, leaf)] = filter row (가장 깊은 경로 우선)
        leaf_full_map: dict[tuple[str, str], dict] = {}
        any_filter_parent: Optional[dict] = None
        for r in rows:
            d = dict(r)
            brand, cats = _parse_filter_name(d["name"])
            if not any_filter_parent:
                any_filter_parent = d
            if not brand or not cats:
                continue
            if len(cats) >= 2:
                full_map[(brand, " > ".join(cats))] = d
                key = (brand, cats[-1].strip())
                prev = leaf_full_map.get(key)
                if not prev or len(cats) > len(_parse_filter_name(prev["name"])[1]):
                    leaf_full_map[key] = d
        print(f"[1] SSG 필터 {len(rows):,}개, 풀 path 매핑 {len(full_map):,}개")

        # 2) 보정 대상 상품
        bad = await conn.fetch(SQL_FETCH_BAD_PRODUCTS)
        print(f"[2] 카테고리 leaf-only/empty 상품 {len(bad):,}개")

        # 3) source_url → breadcrumb fetch (rate-limit)
        SEM = asyncio.Semaphore(4)  # 동시 4건
        DELAY = 0.5  # 호출 간 간격

        update_plan: list[tuple] = []
        new_filters: list[
            tuple
        ] = []  # (id, name, parent_id, tenant_id, keyword, brand)
        per_brand_path_cache: dict[tuple[str, str], dict] = dict(full_map)

        processed = 0
        no_url = 0
        no_bc = 0
        no_brand = 0

        async with httpx.AsyncClient(http2=False) as client:

            async def _process(p: dict) -> None:
                nonlocal no_url, no_bc, no_brand
                url = p["source_url"]
                if not url:
                    no_url += 1
                    return
                brand = (p["brand"] or "").strip()
                if not brand:
                    no_brand += 1
                    return
                async with SEM:
                    bc = await _fetch_breadcrumb(client, url)
                    await asyncio.sleep(DELAY)
                if not bc or len(bc) < 2:
                    no_bc += 1
                    return
                cat_path = " > ".join(bc)
                key = (brand, cat_path)
                target = per_brand_path_cache.get(key)
                if not target:
                    # 새 필터 자동생성
                    fid = _gen_filter_id()
                    name_path = "_".join(bc).replace("/", "_")
                    fname = f"SSG_{brand}_{name_path}"
                    parent_id = (
                        any_filter_parent["parent_id"] if any_filter_parent else None
                    )
                    tenant_id = p["tenant_id"] or (
                        any_filter_parent["tenant_id"] if any_filter_parent else None
                    )
                    keyword = any_filter_parent["keyword"] if any_filter_parent else ""
                    new_filters.append(
                        (fid, fname, parent_id, tenant_id, keyword, brand)
                    )
                    target = {
                        "id": fid,
                        "name": fname,
                        "parent_id": parent_id,
                        "tenant_id": tenant_id,
                        "keyword": keyword,
                    }
                    per_brand_path_cache[key] = target

                c1 = bc[0] if len(bc) > 0 else None
                c2 = bc[1] if len(bc) > 1 else None
                c3 = bc[2] if len(bc) > 2 else None
                c4 = bc[3] if len(bc) > 3 else None
                update_plan.append((target["id"], cat_path, c1, c2, c3, c4, p["id"]))

            tasks = [_process(dict(p)) for p in bad]
            for i in range(0, len(tasks), 50):
                await asyncio.gather(*tasks[i : i + 50])
                processed = min(i + 50, len(tasks))
                print(
                    f"   진행 {processed:,}/{len(tasks):,} "
                    f"(업데이트 {len(update_plan):,}, 신규필터 {len(new_filters):,}, "
                    f"url없음 {no_url:,}, brand없음 {no_brand:,}, 파싱실패 {no_bc:,})"
                )

        print(f"[3] 신규 필터 {len(new_filters):,}개, 상품 보정 {len(update_plan):,}개")
        print(
            f"    skip — url없음 {no_url:,}, brand없음 {no_brand:,}, bc파싱실패 {no_bc:,}"
        )

        if update_plan[:3]:
            print("    예시:")
            for u in update_plan[:3]:
                print(f"      product={u[6]} → filter={u[0]} cat='{u[1]}'")

        if dry_run:
            print("[DRY-RUN] 변경 없음. --apply 로 실제 적용.")
            return

        # 4) 신규 필터 INSERT
        if new_filters:
            print("[4] 신규 필터 INSERT...")
            async with conn.transaction():
                for f in new_filters:
                    fid, fname, parent_id, tenant_id, keyword, _brand = f
                    await conn.execute(
                        SQL_INSERT_FILTER,
                        fid,
                        fname,
                        parent_id,
                        tenant_id,
                        keyword,
                        _brand,
                    )
            print(f"    완료 — {len(new_filters):,}개")

        # 5) 상품 UPDATE
        print("[5] 상품 UPDATE 실행 중...")
        async with conn.transaction():
            for u in update_plan:
                await conn.execute(SQL_UPDATE_PRODUCT, *u)
        print(f"    완료 — {len(update_plan):,}개 상품 보정")

        # 6) 사용처 0건 leaf-only 필터 삭제
        # 이전에 보정된 상품의 옛 filter_id 가 비었으면 leaf-only 필터 삭제
        # (필터명 토큰 ≤ 3 인 필터 중 사용처 0인 것)
        leaf_only_ids = [
            d["id"]
            for d in (dict(r) for r in rows)
            if len(_parse_filter_name(d["name"])[1]) <= 1
        ]
        if leaf_only_ids:
            counts = await conn.fetch(SQL_COUNT_USAGE, leaf_only_ids)
            still_used = {r["search_filter_id"] for r in counts if r["n"] > 0}
            to_delete = [i for i in leaf_only_ids if i not in still_used]
            print(f"[6] 사용처 0건 leaf-only 필터 삭제: {len(to_delete):,}개")
            if to_delete:
                async with conn.transaction():
                    await conn.execute(SQL_DELETE_FILTERS, to_delete)
                print("    완료")
    finally:
        await conn.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    if not args.dry_run and not args.apply:
        print("--dry-run 또는 --apply 필수")
        return
    asyncio.run(run(dry_run=not args.apply))


if __name__ == "__main__":
    main()
