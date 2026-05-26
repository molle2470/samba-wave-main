"""samba_order에서 status='cancelled'인데 shipping_status가 반품/교환/취소진행 라벨로
남아있는 inconsistent row를 '취소완료'로 일괄 정정.

이슈 #224 backfill — main 보호 로직 추가(order.py 4곳 + 일괄 SQL) 이전 누적분 정리.

사용법 (프로덕션 VM 컨테이너):
    # 1) DRY-RUN — 영향 row 수와 분포 확인
    docker exec samba-samba-api-1 /app/backend/.venv/bin/python \
        -m backend.scripts.backfill_cancel_status_inconsistent --dry-run

    # 2) 실제 UPDATE 실행
    docker exec samba-samba-api-1 /app/backend/.venv/bin/python \
        -m backend.scripts.backfill_cancel_status_inconsistent --apply
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import text

from backend.db.orm import get_write_session

INCONSISTENT_SHIPPING_LABELS = (
    "반품요청",
    "반품완료",
    "반품거부",
    "취소요청",
    "취소처리중",
    "교환요청",
    "교환회수완료",
    "교환재배송",
    "교환완료",
)


async def _run(dry_run: bool) -> int:
    async with get_write_session() as session:
        # 분포 확인
        result = await session.execute(
            text(
                """
                SELECT source, shipping_status, COUNT(*) AS cnt
                FROM samba_order
                WHERE status = 'cancelled'
                  AND shipping_status = ANY(:labels)
                GROUP BY source, shipping_status
                ORDER BY cnt DESC
                """
            ),
            {"labels": list(INCONSISTENT_SHIPPING_LABELS)},
        )
        rows = result.fetchall()
        total = sum(r[2] for r in rows)
        print(f"[backfill] inconsistent row {total}건")
        for r in rows:
            print(f"  source={r[0]!r}  ship={r[1]!r}  cnt={r[2]}")

        if dry_run:
            print("[backfill] DRY-RUN 모드 — UPDATE 미실행")
            return 0

        # 실제 UPDATE
        upd = await session.execute(
            text(
                """
                UPDATE samba_order
                SET shipping_status = '취소완료'
                WHERE status = 'cancelled'
                  AND shipping_status = ANY(:labels)
                """
            ),
            {"labels": list(INCONSISTENT_SHIPPING_LABELS)},
        )
        await session.commit()
        print(f"[backfill] UPDATE 완료 — {upd.rowcount}건 정정")
        return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="영향 row 수만 출력")
    group.add_argument("--apply", action="store_true", help="실제 UPDATE 실행")
    args = parser.parse_args()

    rc = asyncio.run(_run(dry_run=args.dry_run))
    sys.exit(rc)


if __name__ == "__main__":
    main()
