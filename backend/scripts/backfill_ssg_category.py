"""SSG 카테고리 1단계(leaf-only) 상품/필터 보정 스크립트.

배경:
- 크론잡(_run_brand_collect_all_ssg)이 검색결과 detail의 풀 path를 못 가져와
  leaf 단일 토큰(예: '티셔츠')으로 필터 자동생성·상품 저장하던 사고.
- worker.py에 leaf-alias 매칭 폴백을 추가했으나, 기존에 잘못 저장된 상품/필터
  데이터는 직접 보정이 필요하다.

동작:
1) source_site='SSG' 필터 중 풀 path 필터(name 토큰 ≥ 4)와 leaf-only 필터(토큰=3) 식별
2) 같은 브랜드(name 두 번째 토큰) 안에서 leaf-only → 풀 path 매핑 dict 구성
3) leaf-only 필터에 속한 상품의 search_filter_id를 풀 path 필터로 옮기고
   category / category1~4 컬럼을 풀 path로 채움
4) 상품이 0건 남은 leaf-only 필터는 삭제 (선택)

사용:
- VM 컨테이너 내부에서 실행 (samba_auth 의존성 우회):
    docker cp scripts/backfill_ssg_category.py samba-samba-api-1:/tmp/
    docker exec samba-samba-api-1 /app/backend/.venv/bin/python \\
        /tmp/backfill_ssg_category.py --dry-run
    # 결과 확인 후:
    docker exec samba-samba-api-1 /app/backend/.venv/bin/python \\
        /tmp/backfill_ssg_category.py --apply
"""

from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict

import asyncpg

from backend.core.config import settings


SQL_FETCH_FILTERS = """
SELECT id, name, source_brand_name, category_filter
FROM samba_search_filter
WHERE source_site = 'SSG'
  AND name IS NOT NULL
"""

SQL_FETCH_BAD_PRODUCTS = """
SELECT id, search_filter_id, name, category, brand, site_product_id
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

SQL_COUNT_PRODUCTS_BY_FILTER = """
SELECT search_filter_id, COUNT(*) AS n
FROM samba_collected_product
WHERE search_filter_id = ANY($1::text[])
GROUP BY search_filter_id
"""

SQL_DELETE_EMPTY_FILTERS = """
DELETE FROM samba_search_filter
WHERE id = ANY($1::text[])
"""


async def _connect() -> asyncpg.Connection:
    """`backend.core.config.settings` 의 write DB 파라미터로 직접 연결."""
    return await asyncpg.connect(
        user=settings.write_db_user,
        password=settings.write_db_password,
        host=settings.write_db_host,
        port=settings.write_db_port,
        database=settings.write_db_name,
        ssl=False,
    )


def _parse_filter_name(name: str) -> tuple[str, list[str]]:
    """'SSG_브랜드_대_중_소' → ('브랜드', ['대','중','소'])."""
    parts = [p for p in name.split("_") if p]
    if len(parts) < 2 or parts[0] != "SSG":
        return ("", [])
    brand = parts[1]
    cats = parts[2:]
    return (brand, cats)


async def run(dry_run: bool, delete_empty: bool) -> None:
    conn = await _connect()
    try:
        rows = await conn.fetch(SQL_FETCH_FILTERS)
        print(f"[1] SSG 필터 총 {len(rows):,}개 로드")

        # 같은 브랜드 안에서 leaf → 풀 path 필터 매핑
        # full_map[(brand, leaf)] = filter row (가장 깊은 path 우선)
        full_map: dict[tuple[str, str], dict] = {}
        leaf_only_filters: list[dict] = []
        for r in rows:
            brand, cats = _parse_filter_name(r["name"])
            if not brand or not cats:
                continue
            if len(cats) >= 2:
                # 풀 path 필터. leaf 알리아스 등록 (같은 brand 안에서 충돌 시
                # 더 깊은 path가 우선)
                leaf = cats[-1].strip()
                key = (brand, leaf)
                prev = full_map.get(key)
                if not prev or len(cats) > len(_parse_filter_name(prev["name"])[1]):
                    full_map[key] = dict(r)
            else:
                # leaf-only(또는 1단계만) 필터
                leaf_only_filters.append(dict(r))

        print(
            f"[2] 풀 path 매핑 {len(full_map):,}개, leaf-only 필터 {len(leaf_only_filters):,}개"
        )

        # leaf-only filter id → 매칭 풀 path 필터 row
        filter_replace: dict[str, dict] = {}
        unmatched_filters: list[dict] = []
        for lf in leaf_only_filters:
            brand, cats = _parse_filter_name(lf["name"])
            leaf = (cats[-1] if cats else "").strip()
            if not leaf:
                unmatched_filters.append(lf)
                continue
            target = full_map.get((brand, leaf))
            if target and target["id"] != lf["id"]:
                filter_replace[lf["id"]] = target
            else:
                unmatched_filters.append(lf)

        print(f"[3] 보정 가능 leaf-only 필터: {len(filter_replace):,}개")
        print(f"    매칭 실패(풀 path 부재): {len(unmatched_filters):,}개")
        if unmatched_filters[:5]:
            print("    예시:")
            for lf in unmatched_filters[:5]:
                print(f"      - {lf['name']}")

        # 잘못된 상품 SELECT
        bad_products = await conn.fetch(SQL_FETCH_BAD_PRODUCTS)
        print(f"[4] 카테고리 leaf-only 상품 {len(bad_products):,}개")

        update_plan: list[tuple] = []
        product_unmatched = 0
        per_filter_moved: dict[str, int] = defaultdict(int)
        for p in bad_products:
            sfid = p["search_filter_id"]
            target = filter_replace.get(sfid) if sfid else None
            if not target:
                # search_filter_id가 풀 path 필터인 경우 — 상품 category만
                # 풀 path로 보정(필터는 그대로 두고 카테고리 컬럼만)
                if sfid and sfid not in filter_replace:
                    # 풀 path 필터일 가능성: filters 목록에서 찾기
                    _src = next((dict(r) for r in rows if r["id"] == sfid), None)
                    if _src:
                        _b, _c = _parse_filter_name(_src["name"])
                        if len(_c) >= 2:
                            target = _src
                if not target:
                    product_unmatched += 1
                    continue

            _b, _c = _parse_filter_name(target["name"])
            cat_path = " > ".join(_c)
            c1 = _c[0] if len(_c) > 0 else None
            c2 = _c[1] if len(_c) > 1 else None
            c3 = _c[2] if len(_c) > 2 else None
            c4 = _c[3] if len(_c) > 3 else None
            update_plan.append((target["id"], cat_path, c1, c2, c3, c4, p["id"]))
            per_filter_moved[target["id"]] += 1

        print(
            f"[5] 상품 보정 대상: {len(update_plan):,}개, 매칭 실패: {product_unmatched:,}개"
        )
        if update_plan[:3]:
            print("    예시:")
            for u in update_plan[:3]:
                print(f"      product={u[6]} → filter={u[0]} cat='{u[1]}'")

        if dry_run:
            print("[DRY-RUN] 변경 없음. --apply 로 실제 적용.")
            return

        # 실제 UPDATE
        print("[6] 상품 UPDATE 실행 중...")
        async with conn.transaction():
            for u in update_plan:
                await conn.execute(SQL_UPDATE_PRODUCT, *u)
        print(f"    완료 — {len(update_plan):,}개 상품 보정")

        # 빈 leaf-only 필터 삭제 (옵션)
        if delete_empty and filter_replace:
            ids = list(filter_replace.keys())
            counts = await conn.fetch(SQL_COUNT_PRODUCTS_BY_FILTER, ids)
            still_used = {r["search_filter_id"] for r in counts if r["n"] > 0}
            to_delete = [i for i in ids if i not in still_used]
            print(f"[7] 사용처 0건 leaf-only 필터 삭제: {len(to_delete):,}개")
            if to_delete:
                async with conn.transaction():
                    await conn.execute(SQL_DELETE_EMPTY_FILTERS, to_delete)
                print("    완료")
    finally:
        await conn.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="변경 사항 미리보기")
    ap.add_argument("--apply", action="store_true", help="실제 적용")
    ap.add_argument(
        "--delete-empty",
        action="store_true",
        help="apply 후 사용처 0건 leaf-only 필터도 삭제",
    )
    args = ap.parse_args()

    if not args.dry_run and not args.apply:
        print("--dry-run 또는 --apply 중 하나는 필수")
        return

    asyncio.run(run(dry_run=not args.apply, delete_empty=args.delete_empty))


if __name__ == "__main__":
    main()
