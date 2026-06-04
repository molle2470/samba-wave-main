"""orphan preemptive failed_at 일회성 청소 [2026-06-04].

fire-and-forget transmit task 가 배포 재시작/cancel 로 사라져 안 떼진 가짜 쪽지
(failure_count=0 + sent_at 존재 + failed_at>sent_at) 의 failed_at/failure_count 키만
계정별 atomic 제거. 전 사이트. 진짜 실패(fc>=1)와 미전송(sent_at=None)은 보존.

제거는 PostgreSQL jsonb `#-` 연산(계정별 nested path 삭제) — `||` merge 는 키 추가만
가능해 삭제 불가. 같은 상품의 멀쩡한 계정 키는 건드리지 않음(단일 UPDATE atomic, race-safe).

사용:
  python cleanup_orphan_preemptive_failed_at.py            # DRY-RUN (집계+샘플만)
  python cleanup_orphan_preemptive_failed_at.py verify     # 청소 후 재측정
  python cleanup_orphan_preemptive_failed_at.py apply       # 전량 실제 청소
  python cleanup_orphan_preemptive_failed_at.py apply 200   # 200개만(소량 검증용)
"""

import asyncio
import json
import sys

import asyncpg

from backend.core.config import settings

MODE = sys.argv[1] if len(sys.argv) > 1 else "dry"
LIMIT = int(sys.argv[2]) if len(sys.argv) > 2 else 0  # 0 = 전량


def _targets(lsd: dict) -> list[str]:
    """청소 대상 계정 키 목록 (가짜 preemptive 쪽지)."""
    out = []
    for acc, v in (lsd or {}).items():
        if not isinstance(v, dict):
            continue
        fa = v.get("failed_at")
        if not fa:
            continue
        sa = v.get("sent_at")
        fc = int(v.get("failure_count") or 0)
        # 가짜 쪽지: fc==0(실패경로 미경유) + 과거 전송이력 있음(sa) + 현재 stuck(fa>sa)
        if fc == 0 and sa and str(fa) > str(sa):
            out.append(acc)
    return out


async def _connect():
    return await asyncpg.connect(
        host=settings.write_db_host,
        port=int(settings.write_db_port),
        user=settings.write_db_user,
        password=settings.write_db_password,
        database=settings.write_db_name,
        ssl=False,
    )


async def _measure(c):
    n = await c.fetchval(
        "SELECT count(*) FROM samba_collected_product"
        " WHERE status='registered'"
        " AND CAST(last_sent_data AS text) LIKE '%failed_at%'"
    )
    print(f"[현재 failed_at 보유 등록상품] {n:,}")
    return n


async def _clean_one(c, pid: str, lsd: dict, targets: list[str]) -> bool:
    """단일 상품의 대상 계정 failed_at/failure_count 키 atomic 제거."""
    expr = "CAST(last_sent_data AS jsonb)"
    args: list = []
    n = 0
    for acc in targets:
        n += 1
        args.append([acc, "failed_at"])
        expr += f" #- CAST(${n} AS text[])"
        n += 1
        args.append([acc, "failure_count"])
        expr += f" #- CAST(${n} AS text[])"
    n += 1
    args.append(pid)
    sql = (
        "UPDATE samba_collected_product"
        f" SET last_sent_data=({expr})::json, updated_at=NOW()"
        f" WHERE id=${n}"
    )
    await c.execute(sql, *args)
    return True


async def main():
    c = await _connect()

    if MODE == "verify":
        await _measure(c)
        await c.close()
        return

    rows = await c.fetch(
        "SELECT id, last_sent_data FROM samba_collected_product"
        " WHERE status='registered'"
        " AND CAST(last_sent_data AS text) LIKE '%failed_at%'"
        + (f" LIMIT {LIMIT}" if LIMIT else "")
    )
    print(f"[스캔] failed_at 보유 {len(rows):,}개")

    plan = []  # (pid, targets, lsd)
    n_target_acc = 0
    for r in rows:
        d = r["last_sent_data"]
        if isinstance(d, str):
            d = json.loads(d)
        d = d or {}
        t = _targets(d)
        if t:
            plan.append((r["id"], t, d))
            n_target_acc += len(t)

    print(f"[청소 대상 상품] {len(plan):,}  [대상 계정 키] {n_target_acc:,}")

    # 샘플 1건 — 대상 계정 제거 + 비대상 계정 보존 검증용 표시
    if plan:
        _pid, _t, _d = plan[0]
        _keep = [a for a in _d if a not in _t][:2]
        print(f"\n[샘플] pid={_pid}")
        print(f"  제거대상: {_t}")
        print(f"  보존(비대상): {_keep}")

    if MODE != "apply":
        print("\n[DRY-RUN] 실제 변경 안 함. 실행하려면: apply")
        await c.close()
        return

    print("\n[APPLY] 청소 시작...")
    done = 0
    for pid, targets, _d in plan:
        # fresh re-read — 스캔~실행 사이 오토튠 전송 성공으로 이미 깨끗해졌으면 skip
        cur = await c.fetchval(
            "SELECT last_sent_data FROM samba_collected_product WHERE id=$1", pid
        )
        if isinstance(cur, str):
            cur = json.loads(cur)
        fresh_t = _targets(cur or {})
        if not fresh_t:
            continue
        await _clean_one(c, pid, cur, fresh_t)
        done += 1
        if done % 500 == 0:
            print(f"  진행 {done:,}/{len(plan):,}", flush=True)

    print(f"[APPLY 완료] 청소 상품 {done:,}")
    await _measure(c)
    await c.close()


asyncio.run(main())
