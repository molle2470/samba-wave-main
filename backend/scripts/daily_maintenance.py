"""
daily_maintenance.py — 일일 자동화 유지보수

매일 실행 작업:
  1. 미매핑 카테고리 매핑 (6대 마켓)
  2. AI 태그 없는 그룹 태그 설정
  3. 정책 없는 그룹 가디 정책 설정
  4. 품절 상품 마켓 삭제 및 DB 삭제
  5. 모든 브랜드 상품 추가수집 잡 생성

사용법:
  python scripts/daily_maintenance.py

환경변수 (선택, task4 마켓삭제 API 호출용):
  SAMBA_ADMIN_EMAIL     관리자 이메일
  SAMBA_ADMIN_PASSWORD  관리자 비밀번호
  SAMBA_BACKEND_URL     백엔드 URL (기본: http://localhost:8080, 로컬: http://localhost:28080)
"""

import asyncio
import base64
import json
import os
import re
import sys
import time
from collections import defaultdict
from typing import Optional

import asyncpg
import bcrypt
import httpx
import ulid

sys.stdout.reconfigure(encoding="utf-8")

# ===== 설정 =====
# 프로덕션(Docker): WRITE_DB_* 환경변수 / 로컬: 기본값(Cloud SQL Auth Proxy 5434)
DB_CONFIG = dict(
    host=os.environ.get("WRITE_DB_HOST", "127.0.0.1"),
    port=int(os.environ.get("WRITE_DB_PORT", "5434")),
    user=os.environ.get("WRITE_DB_USER", "postgres"),
    password=os.environ.get("WRITE_DB_PASSWORD", "gemini0674@@"),
    database=os.environ.get("WRITE_DB_NAME", "railway"),
)
# 프로덕션 컨테이너 내부에서 호출 시 8080, 로컬 개발 시 28080
BACKEND_URL = os.environ.get("SAMBA_BACKEND_URL", "http://localhost:8080")
SS_BASE = "https://api.commerce.naver.com/external"

MARKETS_6 = ("coupang", "smartstore", "gmarket", "auction", "lotteon", "elevenst")
KIDS_KEYWORDS = {"유아", "아동", "주니어", "키즈", "베이비", "kids", "junior", "baby"}
SYNONYMS: dict[str, list[str]] = {
    "아우터": ["재킷", "점퍼", "코트", "자켓", "바람막이", "패딩", "야상"],
    "상의": ["티셔츠", "셔츠", "니트", "맨투맨", "후드티", "블라우스"],
    "하의": ["바지", "팬츠", "슬랙스", "청바지", "레깅스", "치마"],
    "신발": ["스니커즈", "운동화", "구두", "부츠", "샌들", "슬리퍼"],
    "슈즈": ["신발", "스니커즈", "운동화"],
    "가방": ["백팩", "크로스백", "토트백", "숄더백", "클러치"],
    "재킷": ["자켓", "점퍼", "바람막이"],
    "자켓": ["재킷", "점퍼", "바람막이"],
    "점퍼": ["재킷", "자켓", "바람막이"],
    "펌프스": ["힐", "구두"],
    "로퍼": ["플랫슈즈", "단화"],
    "뮬": ["슬리퍼", "로퍼"],
    "런닝화": ["워킹화", "운동화"],
    "워킹화": ["런닝화", "운동화"],
    "등산": ["아웃도어", "트레킹"],
    "트레킹": ["등산", "아웃도어"],
    "후드티": ["맨투맨", "후드"],
    "맨투맨": ["후드티", "스웨트셔츠"],
    "반바지": ["숏팬츠"],
    "배낭": ["백팩"],
    "백팩": ["배낭"],
    "이지웨어": ["잠옷", "홈웨어"],
    "홈웨어": ["잠옷", "이지웨어"],
    "속옷": ["언더웨어"],
    "언더웨어": ["속옷"],
    "정장화": ["구두", "로퍼"],
    "다운": ["패딩"],
    "패딩": ["다운"],
    "힙색": ["슬링백", "웨이스트백"],
    "슬링백": ["힙색"],
    "크로스백": ["숄더백"],
    "숄더백": ["크로스백"],
    "사파리": ["야상"],
    "야상": ["사파리"],
    "캐리어": ["여행가방"],
    "조끼": ["베스트"],
    "베스트": ["조끼"],
    "가디건": ["카디건"],
    "카디건": ["가디건"],
}


# ===== 공통 유틸 =====
def parse_json(val):
    if val is None:
        return []
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return []
    return val if isinstance(val, list) else []


def is_kids(path: str) -> bool:
    return any(kw in path.lower() for kw in KIDS_KEYWORDS)


def extract_keywords(path: str) -> tuple[list[str], list[str]]:
    clean = re.sub(r"신세계몰메인매장\s*>\s*", "", path)
    raw = [
        k.strip() for k in re.split(r"[>/\s]+", clean.lower()) if len(k.strip()) >= 2
    ]
    expanded = list(raw)
    for kw in raw:
        if kw in SYNONYMS:
            expanded.extend(SYNONYMS[kw])
    return raw, list(dict.fromkeys(expanded))


def find_best_category(
    cats: list[str], source_cat: str, hint_cats: list[str]
) -> Optional[str]:
    hint_kws_raw: list[str] = []
    for h in hint_cats:
        raw, _ = extract_keywords(h)
        hint_kws_raw.extend(raw)
    src_raw, _ = extract_keywords(source_cat)
    all_raw = list(dict.fromkeys(hint_kws_raw + src_raw))
    _, all_expanded = extract_keywords(" ".join(all_raw))
    original_set = set(all_raw)
    source_is_kids = is_kids(source_cat) or any(is_kids(h) for h in hint_cats)
    scored = []
    for cat in cats:
        if is_kids(cat) != source_is_kids:
            continue
        lower = cat.lower()
        score = 0
        for kw in all_expanded:
            w = 3 if kw in original_set else 1
            if kw in lower:
                score += w * 2
            else:
                for seg in re.split(r"[>/\s]+", lower):
                    if seg and (kw in seg or seg in kw):
                        score += w
                        break
        if score >= 2:
            scored.append((score, len(cat), cat))
    if not scored:
        return None
    scored.sort(key=lambda x: (-x[0], x[1]))
    return scored[0][2]


# ===== SS 태그 인증 =====
async def get_ss_token(
    client_id: str, client_secret: str, http: httpx.AsyncClient
) -> str | None:
    timestamp = int(time.time() * 1000)
    password = f"{client_id}_{timestamp}"
    hashed = bcrypt.hashpw(password.encode("utf-8"), client_secret.encode("utf-8"))
    sign = base64.standard_b64encode(hashed).decode("utf-8")
    resp = await http.post(
        f"{SS_BASE}/v1/oauth2/token",
        data={
            "client_id": client_id,
            "timestamp": timestamp,
            "client_secret_sign": sign,
            "grant_type": "client_credentials",
            "type": "SELF",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    if resp.status_code == 200:
        return resp.json().get("access_token")
    return None


async def validate_tag(tag: str, token: str, http: httpx.AsyncClient) -> str | None:
    for attempt in range(3):
        try:
            if attempt > 0:
                await asyncio.sleep(1.0 * (2 ** (attempt - 1)))
            resp = await http.get(
                f"{SS_BASE}/v2/tags/recommend-tags",
                headers={"Authorization": f"Bearer {token}"},
                params={"keyword": tag},
            )
            if resp.status_code == 429:
                await asyncio.sleep(2.0)
                continue
            if resp.status_code != 200:
                return None
            data = resp.json()
            results = (
                data
                if isinstance(data, list)
                else (
                    data.get("tags") or data.get("contents") or data.get("data") or []
                )
            )
            for r in results:
                text_val = r.get("text") or r.get("tag")
                if text_val and text_val == tag:
                    return text_val
            return None
        except Exception:
            return None
    return None


# ===== TASK 1: 카테고리 매핑 =====
async def task_category_mapping(conn: asyncpg.Connection) -> dict:
    print("\n[TASK 1] 미매핑 카테고리 매핑 시작")

    # SSG 카테고리 목록 (힌트용)
    ssg_tree = await conn.fetchrow(
        "SELECT cat1 FROM samba_category_tree WHERE site_name='SSG'"
    )
    ssg_cats: list[str] = []
    if ssg_tree and ssg_tree["cat1"]:
        ssg_cats = (
            json.loads(ssg_tree["cat1"])
            if isinstance(ssg_tree["cat1"], str)
            else ssg_tree["cat1"]
        )

    # 11번가 카테고리 목록
    tree_11st = await conn.fetchrow(
        "SELECT cat1 FROM samba_category_tree WHERE site_name='11st'"
    )
    cats_11st: list[str] = []
    if tree_11st and tree_11st["cat1"]:
        cats_11st = (
            json.loads(tree_11st["cat1"])
            if isinstance(tree_11st["cat1"], str)
            else tree_11st["cat1"]
        )

    # 미매핑 항목 조회 (6대 마켓 중 하나라도 없는 경우)
    rows = await conn.fetch("""
        SELECT id, source_site, source_category, target_mappings::text AS tm
        FROM samba_category_mapping
        WHERE NOT (
            (target_mappings::jsonb ? 'coupang') AND (target_mappings::jsonb->>'coupang') > ''
            AND (target_mappings::jsonb ? 'smartstore') AND (target_mappings::jsonb->>'smartstore') > ''
            AND (target_mappings::jsonb ? 'gmarket') AND (target_mappings::jsonb->>'gmarket') > ''
            AND (target_mappings::jsonb ? 'auction') AND (target_mappings::jsonb->>'auction') > ''
            AND (target_mappings::jsonb ? 'lotteon') AND (target_mappings::jsonb->>'lotteon') > ''
            AND (target_mappings::jsonb ? 'elevenst') AND (target_mappings::jsonb->>'elevenst') > ''
        )
        ORDER BY source_site, source_category
    """)

    if not rows:
        print("  미매핑 없음 — 완료")
        return {"mapped": 0, "failed": 0}

    print(f"  처리 대상: {len(rows):,}개")
    mapped = failed = 0

    for r in rows:
        tm = json.loads(r["tm"]) if r["tm"] else {}
        source_cat = r["source_category"]

        # 기존 매핑을 힌트로 수집 (SSG 포함)
        hint_cats = [v for k, v in tm.items() if v and k != "elevenst"]

        # 5대 마켓 (coupang, smartstore, gmarket, auction, lotteon) 매핑
        for market in ("coupang", "smartstore", "gmarket", "auction", "lotteon"):
            if tm.get(market):
                continue
            hints = [v for k, v in tm.items() if v and k != market]
            best = find_best_category(ssg_cats, source_cat, hints)
            if best:
                tm[market] = best

        # elevenst 매핑 (11번가 카테고리 트리 사용)
        if not tm.get("elevenst") and cats_11st:
            hint_cats = [v for k, v in tm.items() if v and k != "elevenst"]
            best_11st = find_best_category(cats_11st, source_cat, hint_cats)
            if best_11st:
                tm["elevenst"] = best_11st

        # 하나라도 신규 매핑됐으면 업데이트
        all_mapped = all(tm.get(m) for m in MARKETS_6)
        if any(tm.get(m) for m in MARKETS_6):
            await conn.execute(
                "UPDATE samba_category_mapping SET target_mappings=$1::json, updated_at=NOW() WHERE id=$2",
                json.dumps(tm, ensure_ascii=False),
                r["id"],
            )
            if all_mapped:
                mapped += 1
            else:
                failed += 1  # 일부만 매핑됨
        else:
            failed += 1

    print(f"  완료: {mapped:,}개 전체매핑, {failed:,}개 부분/미매핑")
    return {"mapped": mapped, "failed": failed}


# ===== TASK 2: AI 태그 설정 =====
async def task_ai_tags(conn: asyncpg.Connection) -> dict:
    print("\n[TASK 2] AI 태그 설정 시작")

    # SS 토큰 획득
    ss_accounts = await conn.fetch(
        "SELECT api_key, additional_fields FROM samba_market_account "
        "WHERE market_type='smartstore' AND is_active=true LIMIT 3"
    )
    http = httpx.AsyncClient(timeout=30)
    ss_token = ss_cid = ss_csec = None

    for acc in ss_accounts:
        af = parse_json(acc["additional_fields"]) if acc["additional_fields"] else {}
        cid = (af.get("clientId") if isinstance(af, dict) else None) or acc["api_key"]
        csec = af.get("clientSecret", "") if isinstance(af, dict) else ""
        if cid and csec:
            ss_token = await get_ss_token(cid, csec, http)
            if ss_token:
                ss_cid, ss_csec = cid, csec
                print(f"  SS 토큰 획득: {cid[:10]}...")
                break

    if not ss_token:
        print("  SS 토큰 실패 — AI 태그 건너뜀")
        await http.aclose()
        return {"applied": 0, "failed": 0, "error": "SS 토큰 실패"}

    # 기존 태그 패턴 DB 구축
    existing = await conn.fetch("""
        SELECT DISTINCT ON (cp.brand, cp.category1, cp.category2, cp.category3)
            cp.brand, cp.category1, cp.category2, cp.category3,
            cp.tags, cp.seo_keywords
        FROM samba_collected_product cp
        WHERE cp.tags::text LIKE '%__ai_tagged__%'
        ORDER BY cp.brand, cp.category1, cp.category2, cp.category3, cp.updated_at DESC
    """)
    tag_db: dict[tuple, dict] = {}
    brand_fallback: dict[str, dict] = {}

    def _register(brand, cat, tags, seo):
        parts = [cat] + (cat.split("/") if "/" in cat else [])
        for part in parts:
            part = part.strip()
            if not part:
                continue
            for b in [brand, ""]:
                key = (b, part)
                if key not in tag_db:
                    tag_db[key] = {"tags": tags, "seo": seo}

    for r in existing:
        brand = r["brand"] or ""
        cats = [r["category1"] or "", r["category2"] or "", r["category3"] or ""]
        tags = [t for t in parse_json(r["tags"]) if not t.startswith("__")]
        seo = parse_json(r["seo_keywords"])
        if tags:
            for c in cats:
                if c:
                    _register(brand, c, tags, seo)
            if brand and brand not in brand_fallback:
                brand_fallback[brand] = {"tags": tags, "seo": seo}

    print(f"  기존 패턴: {len(tag_db):,}개, 브랜드 폴백: {len(brand_fallback):,}개")

    # 미태그 그룹 조회
    untagged = await conn.fetch("""
        SELECT DISTINCT sf.id AS filter_id, sf.name AS filter_name
        FROM samba_search_filter sf
        JOIN samba_collected_product cp ON cp.search_filter_id = sf.id
        WHERE sf.is_folder = false
          AND (cp.tags IS NULL OR cp.tags::text NOT LIKE '%__ai_tagged__%')
        ORDER BY sf.name
    """)
    total = len(untagged)
    if not total:
        print("  미태그 그룹 없음 — 완료")
        await http.aclose()
        return {"applied": 0, "failed": 0}

    print(f"  처리 대상: {total:,}개 그룹")
    ss_cache: dict[str, str | None] = {}
    ss_token_refresh_time = time.time()
    applied = no_match = 0

    for idx, group in enumerate(untagged):
        filter_id = group["filter_id"]
        filter_name = group["filter_name"]

        # 대표 상품 정보
        rep = await conn.fetchrow(
            """
            SELECT brand, category1, category2, category3, category4
            FROM samba_collected_product
            WHERE search_filter_id=$1
            ORDER BY created_at LIMIT 1
        """,
            filter_id,
        )
        if not rep:
            continue

        brand = rep["brand"] or ""
        cats = [
            c
            for c in [
                rep["category1"],
                rep["category2"],
                rep["category3"],
                rep["category4"],
            ]
            if c
        ]

        # 후보 태그 조회
        candidate_tags, cand_seo = [], []
        for cat in reversed(cats):
            for b in [brand, ""]:
                key = (b, cat)
                if key in tag_db:
                    candidate_tags = tag_db[key]["tags"]
                    cand_seo = tag_db[key]["seo"]
                    break
            if candidate_tags:
                break

        if not candidate_tags:
            for cat in reversed(cats):
                for part in cat.split("/"):
                    part = part.strip()
                    for b in [brand, ""]:
                        key = (b, part)
                        if key in tag_db:
                            candidate_tags = tag_db[key]["tags"]
                            cand_seo = tag_db[key]["seo"]
                            break
                    if candidate_tags:
                        break
                if candidate_tags:
                    break

        if not candidate_tags and brand in brand_fallback:
            candidate_tags = brand_fallback[brand]["tags"]
            cand_seo = brand_fallback[brand]["seo"]

        if not candidate_tags:
            no_match += 1
            continue

        # SS 토큰 갱신 (45분)
        if time.time() - ss_token_refresh_time > 2700:
            new_token = await get_ss_token(ss_cid, ss_csec, http)
            if new_token:
                ss_token = new_token
                ss_token_refresh_time = time.time()

        # SS 태그사전 검증
        validated = []
        for tag in candidate_tags:
            if len(validated) >= 12:
                break
            if tag in ss_cache:
                result = ss_cache[tag]
            else:
                result = await validate_tag(tag, ss_token, http)
                ss_cache[tag] = result
                await asyncio.sleep(0.1)
            if result:
                validated.append(result)
            await asyncio.sleep(0.25)

        if not validated:
            no_match += 1
            continue

        seo_to_apply = validated[:2]
        tags_to_apply = validated[2:12] if len(validated) > 2 else validated[:10]

        # 그룹 전체 상품 적용
        products = await conn.fetch(
            "SELECT id, tags FROM samba_collected_product WHERE search_filter_id=$1",
            filter_id,
        )
        for p in products:
            existing_tags = parse_json(p["tags"]) or []
            preserved = [
                t for t in existing_tags if isinstance(t, str) and t.startswith("__")
            ]
            new_tags = list(
                dict.fromkeys([*preserved, "__ai_tagged__", *tags_to_apply])
            )
            await conn.execute(
                "UPDATE samba_collected_product SET tags=$1::jsonb, seo_keywords=$2::jsonb, updated_at=NOW() WHERE id=$3",
                json.dumps(new_tags, ensure_ascii=False),
                json.dumps(seo_to_apply, ensure_ascii=False),
                p["id"],
            )

        applied += 1
        if applied % 50 == 0 or (idx + 1) % 200 == 0:
            print(f"  [{idx + 1:,}/{total:,}] 적용:{applied:,}, 미매칭:{no_match:,}")

    await http.aclose()
    print(f"  완료: {applied:,}개 그룹 적용, {no_match:,}개 미매칭")
    return {"applied": applied, "no_match": no_match}


# ===== TASK 3: 정책 동기화 =====
async def task_policy_sync(conn: asyncpg.Connection) -> dict:
    """정책 없는 그룹에 같은 (source_site, 브랜드)의 기존 정책을 상속."""
    print("\n[TASK 3] 정책 동기화 시작")

    count_before = await conn.fetchval("""
        SELECT COUNT(*) FROM samba_search_filter
        WHERE is_folder = false AND applied_policy_id IS NULL
    """)

    if count_before == 0:
        print("  정책 미적용 그룹 없음 — 완료")
        return {"applied": 0, "skipped": 0}

    print(f"  정책 없는 그룹: {count_before:,}개")

    # 같은 (source_site, 브랜드) 내 다른 그룹의 정책 상속
    result = await conn.execute("""
        UPDATE samba_search_filter sf1
        SET applied_policy_id = (
            SELECT sf2.applied_policy_id
            FROM samba_search_filter sf2
            WHERE sf2.source_site = sf1.source_site
              AND split_part(sf2.name, '_', 2) = split_part(sf1.name, '_', 2)
              AND sf2.is_folder = false
              AND sf2.applied_policy_id IS NOT NULL
              AND sf2.id != sf1.id
            LIMIT 1
        ), updated_at = NOW()
        WHERE sf1.is_folder = false
          AND sf1.applied_policy_id IS NULL
          AND sf1.source_site IS NOT NULL
          AND sf1.name IS NOT NULL
          AND EXISTS (
              SELECT 1 FROM samba_search_filter sf2
              WHERE sf2.source_site = sf1.source_site
                AND split_part(sf2.name, '_', 2) = split_part(sf1.name, '_', 2)
                AND sf2.is_folder = false
                AND sf2.applied_policy_id IS NOT NULL
                AND sf2.id != sf1.id
          )
    """)
    applied = int(result.split()[-1]) if result else 0
    skipped = count_before - applied

    # 그룹 정책 → 상품에도 동기화
    await conn.execute("""
        UPDATE samba_collected_product cp
        SET applied_policy_id = sf.applied_policy_id, updated_at = NOW()
        FROM samba_search_filter sf
        WHERE cp.search_filter_id = sf.id
          AND sf.is_folder = false
          AND sf.applied_policy_id IS NOT NULL
          AND (cp.applied_policy_id IS NULL OR cp.applied_policy_id != sf.applied_policy_id)
    """)

    print(f"  완료: 상속 {applied:,}개, 스킵(동일브랜드 정책없음) {skipped:,}개")
    return {"applied": applied, "skipped": skipped}


# ===== TASK 4: 품절 처리 =====
async def task_soldout_cleanup(conn: asyncpg.Connection) -> dict:
    print("\n[TASK 4] 품절 상품 처리 시작")

    # 마켓 미등록 품절 → DB 직접 삭제
    no_market_ids = await conn.fetch("""
        SELECT id FROM samba_collected_product
        WHERE sale_status = 'sold_out'
          AND (
            registered_accounts IS NULL
            OR registered_accounts::text = 'null'
            OR registered_accounts::text = '[]'
            OR jsonb_array_length(registered_accounts::jsonb) = 0
          )
    """)

    deleted_db = 0
    if no_market_ids:
        ids = [r["id"] for r in no_market_ids]
        print(f"  미등록 품절 {len(ids):,}개 → DB 삭제")
        await conn.execute(
            "DELETE FROM samba_collected_product WHERE id = ANY($1::text[])", ids
        )
        deleted_db = len(ids)

    # 마켓 등록된 품절 → 백엔드 API 호출
    with_market = await conn.fetch("""
        SELECT id, registered_accounts::text AS ra
        FROM samba_collected_product
        WHERE sale_status = 'sold_out'
          AND registered_accounts IS NOT NULL
          AND registered_accounts::text NOT IN ('null', '[]', '')
          AND jsonb_array_length(registered_accounts::jsonb) > 0
    """)

    market_deleted = market_failed = 0
    if with_market:
        print(f"  마켓 등록 품절 {len(with_market):,}개 → 마켓삭제 시도")
        jwt_token = await _get_backend_jwt()
        api_key = os.environ.get("API_GATEWAY_KEY", "")
        if jwt_token and api_key:
            headers = {
                "Authorization": f"Bearer {jwt_token}",
                "X-Api-Key": api_key,
            }
            # 계정별로 그룹화하여 배치 처리
            acc_product_map: dict[str, list[str]] = defaultdict(list)
            for row in with_market:
                accounts = json.loads(row["ra"]) if row["ra"] else []
                for acc_id in accounts:
                    if acc_id:
                        acc_product_map[acc_id].append(row["id"])

            async with httpx.AsyncClient(timeout=120) as http:
                for acc_id, pids in acc_product_map.items():
                    # 50개씩 배치
                    for i in range(0, len(pids), 50):
                        batch = pids[i : i + 50]
                        try:
                            resp = await http.post(
                                f"{BACKEND_URL}/api/v1/samba/shipments/market-delete",
                                json={
                                    "product_ids": batch,
                                    "target_account_ids": [acc_id],
                                    "log_to_buffer": False,
                                },
                                headers=headers,
                            )
                            if resp.status_code == 200:
                                market_deleted += len(batch)
                            else:
                                market_failed += len(batch)
                                print(
                                    f"  마켓삭제 실패 {acc_id[:15]}: HTTP {resp.status_code} {resp.text[:80]}"
                                )
                        except Exception as e:
                            market_failed += len(batch)
                            print(f"  마켓삭제 오류 {acc_id[:15]}: {e}")
                        await asyncio.sleep(0.5)
        else:
            print(f"  JWT/API키 없음 — 마켓 등록 품절 {len(with_market):,}개 처리 보류")
            market_failed = len(with_market)

    total_soldout = await conn.fetchval(
        "SELECT COUNT(*) FROM samba_collected_product WHERE sale_status='sold_out'"
    )
    print(
        f"  DB삭제:{deleted_db:,}, 마켓삭제:{market_deleted:,}, 보류:{market_failed:,}, "
        f"잔여품절:{total_soldout:,}"
    )
    return {
        "deleted_db": deleted_db,
        "market_deleted": market_deleted,
        "market_failed": market_failed,
    }


async def _get_backend_jwt() -> str | None:
    """JWT 시크릿으로 직접 토큰 생성 (비밀번호 불필요)."""
    import jwt as pyjwt
    from datetime import datetime, timedelta, timezone

    secret = os.environ.get("JWT_SECRET_KEY", "")
    algorithm = os.environ.get("JWT_ALGORITHM", "HS256")
    admin_email = os.environ.get("SAMBA_ADMIN_EMAIL", "cannonfort@naver.com")

    if not secret:
        return None

    try:
        # DB에서 user_id 조회
        conn = await asyncpg.connect(**DB_CONFIG)
        row = await conn.fetchrow(
            "SELECT id FROM samba_user WHERE email=$1 LIMIT 1", admin_email
        )
        await conn.close()
        if not row:
            return None

        expire = datetime.now(timezone.utc) + timedelta(days=1)
        token = pyjwt.encode(
            {"sub": row["id"], "exp": expire, "type": "access"},
            secret,
            algorithm=algorithm,
        )
        return token
    except Exception as e:
        print(f"  JWT 생성 실패: {e}")
        return None


# ===== TASK 5: 브랜드 수집 잡 생성 =====
async def task_brand_collect_jobs(
    conn: asyncpg.Connection, interval_days: int = 5
) -> dict:
    print("\n[TASK 5] 브랜드 수집 잡 생성 시작")

    # 현재 활성 브랜드 목록: source_site별 브랜드 + filter_ids
    filters = await conn.fetch("""
        SELECT id, name, source_site
        FROM samba_search_filter
        WHERE is_folder = false
          AND source_site IS NOT NULL
          AND name IS NOT NULL
        ORDER BY source_site, name
    """)

    # source_site + brand → filter_ids 매핑 (브랜드별 1개 잡)
    # SSG는 leaf-only 사고 차단을 위해 brand 단위가 아닌 filter 단위 잡으로 enqueue
    # → 수동 카테고리 스캔과 동일한 _run_collect → _collect_direct_api 경로 사용.
    brand_map: dict[tuple[str, str], list[str]] = defaultdict(list)
    ssg_brand_filters: dict[str, list[str]] = defaultdict(list)  # brand → [filter_id]
    for f in filters:
        name = f["name"] or ""
        source = f["source_site"] or ""
        parts = name.split("_")
        if len(parts) >= 2:
            brand = parts[1]
            if source == "SSG":
                ssg_brand_filters[brand].append(f["id"])
            else:
                brand_map[(source, brand)].append(f["id"])

    if not brand_map and not ssg_brand_filters:
        print("  브랜드 없음 — 완료")
        return {"created": 0, "skipped": 0}

    # 이미 pending/running인 잡 (소싱처+브랜드) — 중복 방지
    active_jobs = await conn.fetch("""
        SELECT payload
        FROM samba_jobs
        WHERE job_type = 'collect'
          AND status IN ('pending', 'running')
    """)
    active_keys: set[tuple[str, str]] = set()
    for job in active_jobs:
        p = job["payload"] if isinstance(job["payload"], dict) else {}
        s = p.get("source_site", "")
        b = p.get("brand", "")
        if s and b:
            active_keys.add((s, b))

    # 마지막 완료 시각 (소싱처+브랜드별)
    completed_jobs = await conn.fetch("""
        SELECT
            payload->>'source_site' AS source_site,
            payload->>'brand'       AS brand,
            MAX(completed_at)       AS last_completed
        FROM samba_jobs
        WHERE job_type = 'collect'
          AND status = 'completed'
          AND completed_at IS NOT NULL
        GROUP BY payload->>'source_site', payload->>'brand'
    """)
    last_completed: dict[tuple[str, str], object] = {}
    for row in completed_jobs:
        if row["source_site"] and row["brand"]:
            last_completed[(row["source_site"], row["brand"])] = row["last_completed"]

    from datetime import datetime, timezone, timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(days=interval_days)

    created = skipped = 0
    for (source, brand), filter_ids in brand_map.items():
        # 이미 큐에 있으면 skip
        if (source, brand) in active_keys:
            skipped += 1
            continue

        # 마지막 완료가 5일 이내면 skip
        last = last_completed.get((source, brand))
        if last is not None:
            last_aware = (
                last.replace(tzinfo=timezone.utc) if last.tzinfo is None else last
            )
            if last_aware >= cutoff:
                skipped += 1
                continue

        job_id = f"job_{ulid.ULID()}"
        payload = {
            "brand_all": True,
            "source_site": source,
            "keyword": brand,
            "brand": brand,
            "filter_ids": filter_ids,
        }
        await conn.execute(
            """
            INSERT INTO samba_jobs (id, job_type, status, payload, progress, total, current, attempt, created_at)
            VALUES ($1, 'collect', 'pending', $2::json, 0, $3, 0, 0, NOW())
            """,
            job_id,
            json.dumps(payload, ensure_ascii=False),
            len(filter_ids),
        )
        created += 1

    # SSG: filter 단위 잡 enqueue (brand_all 미포함 → 수동 카테고리 스캔과 동일 경로)
    # 5일 인터벌·active 체크는 brand 단위로 유지 — 같은 brand의 filter 잡이 큐에 있거나
    # 최근 완료 기록이 있으면 해당 brand의 모든 filter를 한꺼번에 skip.
    ssg_created = 0
    for brand, filter_ids in ssg_brand_filters.items():
        if ("SSG", brand) in active_keys:
            skipped += 1
            continue
        last = last_completed.get(("SSG", brand))
        if last is not None:
            last_aware = (
                last.replace(tzinfo=timezone.utc) if last.tzinfo is None else last
            )
            if last_aware >= cutoff:
                skipped += 1
                continue
        for filter_id in filter_ids:
            job_id = f"job_{ulid.ULID()}"
            payload = {
                "filter_id": filter_id,
                "source_site": "SSG",
                "brand": brand,  # last_completed/active_keys 매칭용
                "keyword": brand,
            }
            await conn.execute(
                """
                INSERT INTO samba_jobs (id, job_type, status, payload, progress, total, current, attempt, created_at)
                VALUES ($1, 'collect', 'pending', $2::json, 0, 1, 0, 0, NOW())
                """,
                job_id,
                json.dumps(payload, ensure_ascii=False),
            )
            created += 1
            ssg_created += 1

    print(
        f"  생성:{created:,}개(SSG filter별 {ssg_created:,}개 포함), "
        f"스킵(5일이내또는큐존재):{skipped:,}개"
    )
    return {"created": created, "skipped": skipped, "ssg_created": ssg_created}


# ===== TASK 6: 일별 등록상품수 스냅샷 =====
async def task_daily_snapshot(conn: asyncpg.Connection) -> dict:
    """현재 마켓에 1개 이상 등록된 상품수를 KST 기준 오늘 날짜로 스냅샷 저장.

    조건은 build_market_registered_conditions와 동일:
      registered_accounts != NULL/[] AND market_product_nos != NULL/null/{}
    """
    print("\n[TASK 6] 일별 등록상품수 스냅샷 저장 시작")
    row = await conn.fetchrow(
        """
        SELECT COUNT(*) AS cnt FROM samba_collected_product
        WHERE registered_accounts IS NOT NULL
          AND jsonb_typeof(registered_accounts) = 'array'
          AND registered_accounts != '[]'::jsonb
          AND market_product_nos IS NOT NULL
          AND market_product_nos::text != 'null'
          AND market_product_nos::text != '{}'
        """
    )
    count = int(row["cnt"]) if row else 0
    # KST 기준 오늘
    snapshot_date = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 9 * 3600))
    await conn.execute(
        """
        INSERT INTO samba_daily_registered_snapshot (snapshot_date, registered_count)
        VALUES ($1, $2)
        ON CONFLICT (snapshot_date) DO UPDATE
          SET registered_count = EXCLUDED.registered_count,
              created_at = now()
        """,
        snapshot_date,
        count,
    )
    print(f"  스냅샷 저장: {snapshot_date} = {count:,}건")
    return {"date": snapshot_date, "count": count}


# ===== TASK 5b: 신규 그룹 AI 태그 + 가디 정책 확인 =====
async def task_new_group_check(conn: asyncpg.Connection) -> dict:
    """수집 잡 완료 후 생성된 신규 그룹에 AI 태그 + 가디 정책 적용 여부 확인."""
    missing_tag = await conn.fetchval("""
        SELECT COUNT(DISTINCT sf.id) FROM samba_search_filter sf
        JOIN samba_collected_product cp ON cp.search_filter_id = sf.id
        WHERE sf.is_folder = false
          AND (cp.tags IS NULL OR cp.tags::text NOT LIKE '%__ai_tagged__%')
    """)
    missing_policy = await conn.fetchval("""
        SELECT COUNT(*) FROM samba_search_filter
        WHERE is_folder = false AND applied_policy_id IS NULL
    """)
    return {"missing_tag_groups": missing_tag, "missing_policy_groups": missing_policy}


# ===== 메인 =====
async def load_job_config(conn: asyncpg.Connection) -> dict:
    """samba_settings에서 daily_job_config를 읽어 task ON/OFF 및 재수집 주기를 반환"""
    defaults = {
        "task1_enabled": True,
        "task2_enabled": True,
        "task3_enabled": True,
        "task4_enabled": True,
        "task5_enabled": True,
        "recollect_interval_days": 5,
    }
    try:
        row = await conn.fetchrow(
            "SELECT value FROM samba_settings WHERE key = 'daily_job_config' LIMIT 1"
        )
        if row:
            stored = (
                json.loads(row["value"])
                if isinstance(row["value"], str)
                else row["value"]
            )
            if isinstance(stored, dict):
                defaults.update(stored)
    except Exception:
        pass
    return defaults


async def run():
    print("=" * 60)
    print("삼바웨이브 일일 유지보수 시작")
    print(f"시각: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    conn = await asyncpg.connect(**DB_CONFIG)
    results = {}

    try:
        cfg = await load_job_config(conn)
        interval_days = int(cfg.get("recollect_interval_days", 5))
        print(
            f"  설정: 재수집주기={interval_days}일, "
            f"T1={'ON' if cfg['task1_enabled'] else 'OFF'} "
            f"T2={'ON' if cfg['task2_enabled'] else 'OFF'} "
            f"T3={'ON' if cfg['task3_enabled'] else 'OFF'} "
            f"T4={'ON' if cfg['task4_enabled'] else 'OFF'} "
            f"T5={'ON' if cfg['task5_enabled'] else 'OFF'}"
        )

        results["category"] = (
            await task_category_mapping(conn)
            if cfg["task1_enabled"]
            else {"skipped": True}
        )
        results["ai_tags"] = (
            await task_ai_tags(conn) if cfg["task2_enabled"] else {"skipped": True}
        )
        results["policy"] = (
            await task_policy_sync(conn) if cfg["task3_enabled"] else {"skipped": True}
        )
        results["soldout"] = (
            await task_soldout_cleanup(conn)
            if cfg["task4_enabled"]
            else {"skipped": True}
        )
        results["collect"] = (
            await task_brand_collect_jobs(conn, interval_days=interval_days)
            if cfg["task5_enabled"]
            else {"skipped": True}
        )

        # 일별 등록상품수 스냅샷 (config 토글 없음 — 항상 실행)
        results["snapshot"] = await task_daily_snapshot(conn)

        # 수집 잡 생성 직후 미적용 현황 확인 (수집 완료 후 다음 실행에서 처리됨)
        check = await task_new_group_check(conn)

    finally:
        await conn.close()

    print("\n" + "=" * 60)
    print("일일 유지보수 완료")
    print(f"  카테고리 매핑: {results['category']}")
    print(f"  AI 태그:       {results['ai_tags']}")
    print(f"  정책 동기화:   {results['policy']}")
    print(f"  품절 처리:     {results['soldout']}")
    print(f"  수집 잡 생성:  {results['collect']}")
    print(f"  스냅샷:        {results.get('snapshot')}")
    print(
        f"  신규그룹 확인: 미태그={check['missing_tag_groups']:,}, 미정책={check['missing_policy_groups']:,}"
    )
    print("=" * 60)


async def run_tasks(task_ids: list[int]):
    """지정한 태스크만 실행 (VM cron용). 예: run_tasks([5, 4, 3])"""
    print("=" * 60)
    print(f"삼바웨이브 유지보수 시작 (tasks={task_ids})")
    print(f"시각: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    conn = await asyncpg.connect(**DB_CONFIG)
    results = {}

    try:
        if 5 in task_ids:
            results["collect"] = await task_brand_collect_jobs(conn, interval_days=5)
        if 4 in task_ids:
            results["soldout"] = await task_soldout_cleanup(conn)
        if 3 in task_ids:
            results["policy"] = await task_policy_sync(conn)
        if 2 in task_ids:
            results["ai_tags"] = await task_ai_tags(conn)
        if 1 in task_ids:
            results["category"] = await task_category_mapping(conn)
        if 6 in task_ids:
            results["snapshot"] = await task_daily_snapshot(conn)
    finally:
        await conn.close()

    print("\n" + "=" * 60)
    print("완료")
    for k, v in results.items():
        print(f"  {k}: {v}")
    print("=" * 60)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--tasks",
        type=str,
        default=None,
        help="실행할 태스크 번호 (쉼표 구분, 예: 5,4,3). 미지정 시 전체 실행.",
    )
    args = parser.parse_args()

    if args.tasks:
        task_ids = [int(t.strip()) for t in args.tasks.split(",")]
        asyncio.run(run_tasks(task_ids))
    else:
        asyncio.run(run())
