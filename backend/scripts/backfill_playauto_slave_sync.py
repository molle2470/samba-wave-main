"""플레이오토 슬레이브 stale 백필 — 전체 등록상품 가격/재고 1회 재전송.

버그 기간(2026-05-15~06-08) 경량 PATCH가 use_no_edit_slave=True로 호출돼
마스터만 갱신되고 슬레이브(마켓별 등록 상품)는 stale. 9add2432로 수정 배포 완료.
이 스크립트는 마스터 현재 가격/재고를 모든 슬레이브에 일괄 재동기화한다.

무상태 커서 방식: 컨테이너 /tmp는 배포(컨테이너 재생성)마다 날아가므로
진행 커서는 호스트가 보관하고 인자로 넘긴다. 스크립트는 id > cursor 인
상품을 ORDER BY id 로 청크만 처리하고 마지막 id를 stdout에 출력한다.

모드:
  count  — 플레이오토 계정 + 등록상품 개수 (읽기전용)
  run    — id > --cursor 인 상품을 --batch 씩 --max-batches 만큼 재전송.
           각 배치 후 PROGRESS, 종료 시 CURSOR=<id> / STATUS=DONE|MORE 출력.

stdout 계약(호스트 오케스트레이터가 파싱):
  PROGRESS processed=<n> ok=<n> fail=<n> last=<id>
  CURSOR=<last_processed_id>
  STATUS=DONE   (이 커서 이후 남은 상품 없음)
  STATUS=MORE   (아직 남음 — 호스트가 커서 저장 후 재호출)
"""

import argparse
import asyncio
import time

from sqlalchemy import select, text

from backend.db.orm import get_read_session, get_write_session
from backend.domain.samba.account.model import SambaMarketAccount
from backend.domain.samba.shipment.repository import SambaShipmentRepository
from backend.domain.samba.shipment.service import SambaShipmentService


async def _playauto_account_ids(session) -> list[str]:
    accs = (
        await session.execute(
            select(SambaMarketAccount.id).where(
                SambaMarketAccount.market_type == "playauto"
            )
        )
    ).all()
    return [a[0] for a in accs]


async def _next_product_ids(
    session, account_id: str, cursor: str, limit: int
) -> list[str]:
    """id > cursor 인, 해당 플레이오토 계정 등록상품 id (ORDER BY id, LIMIT).

    raw SQL `@> CAST(:v AS jsonb)` — ORM cast(JSONB)는 이중인코딩 0건 함정.
    """
    rows = (
        await session.execute(
            text(
                "SELECT id FROM samba_collected_product "
                "WHERE registered_accounts @> CAST(:v AS jsonb) "
                "AND id > :cur ORDER BY id ASC LIMIT :lim"
            ),
            {"v": f'["{account_id}"]', "cur": cursor, "lim": limit},
        )
    ).all()
    return [r[0] for r in rows]


async def _count_for(session, account_id: str) -> int:
    return (
        await session.execute(
            text(
                "SELECT count(*) FROM samba_collected_product "
                "WHERE registered_accounts @> CAST(:v AS jsonb)"
            ),
            {"v": f'["{account_id}"]'},
        )
    ).scalar() or 0


async def cmd_count() -> None:
    async with get_read_session() as session:
        ids = await _playauto_account_ids(session)
        print(f"플레이오토 계정 {len(ids):,}개")
        total = 0
        for aid in ids:
            c = await _count_for(session, aid)
            total += c
            print(f"  {aid} 등록상품={c:,}건")
        print(f"총 {total:,}건")


async def _retransmit(account_id: str, product_ids: list[str]) -> tuple[int, int]:
    """묶음 재전송 → (성공, 실패)."""
    async with get_write_session() as session:
        svc = SambaShipmentService(SambaShipmentRepository(session), session)
        res = await svc.start_update(
            product_ids,
            ["price", "stock"],
            [account_id],
            skip_unchanged=False,
            skip_refresh=True,
            skip_policy_account_filter=True,
        )
        await session.commit()
    ok = fail = 0
    for r0 in res.get("results") or []:
        if (r0.get("transmit_result") or {}).get(account_id) == "success":
            ok += 1
        else:
            fail += 1
    return ok, fail


async def cmd_run(cursor: str, batch: int, max_batches: int) -> None:
    # 플레이오토 계정은 실측 1개. 여러 개여도 단일 커서 공간을 쓰려면
    # 계정별로 돌려야 하지만, 현재 1개이므로 첫 계정만 대상으로 한다.
    async with get_read_session() as session:
        acc_ids = await _playauto_account_ids(session)
    if not acc_ids:
        print("CURSOR=" + cursor)
        print("STATUS=DONE")
        return
    account_id = acc_ids[0]

    last = cursor
    total_ok = total_processed = total_fail = 0
    status = "DONE"
    for _ in range(max_batches):
        async with get_read_session() as session:
            chunk = await _next_product_ids(session, account_id, last, batch)
        if not chunk:
            status = "DONE"
            break
        t0 = time.monotonic()
        ok, fail = await _retransmit(account_id, chunk)
        dt = time.monotonic() - t0
        last = chunk[-1]
        total_ok += ok
        total_fail += fail
        total_processed += len(chunk)
        print(
            f"PROGRESS processed={total_processed} ok={total_ok} "
            f"fail={total_fail} last={last} batch_sec={dt:.1f}",
            flush=True,
        )
        if len(chunk) < batch:
            status = "DONE"
            break
        status = "MORE"
    print(f"CURSOR={last}", flush=True)
    print(f"STATUS={status}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["count", "run"])
    ap.add_argument("--cursor", default="")
    ap.add_argument("--batch", type=int, default=50)
    ap.add_argument("--max-batches", type=int, default=20)
    args = ap.parse_args()

    if args.mode == "count":
        asyncio.run(cmd_count())
    else:
        asyncio.run(cmd_run(args.cursor, args.batch, args.max_batches))


if __name__ == "__main__":
    main()
