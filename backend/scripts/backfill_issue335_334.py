"""issue #335/#334 기존 오염 row 백필.

A+B 배포 후 실행 (안 그러면 다음 sync 재오염).
기본 dry-run(롤백). 실제 반영은 `--apply` 인자.

대상:
1) #335: status='cancelled' 인데 shipping_status 가 반품/교환 라벨 → '취소완료' 정정
2) #335: 취소완료 주문의 stale 활성 samba_return → auto-close (배포된 Part B와 동일)
3) #334: completion_detail 프론트 어휘 잔존분 → 백엔드 어휘로 정정
"""

import asyncio
import sys

from sqlalchemy import text

from backend.db.orm import get_write_session

APPLY = "--apply" in sys.argv


async def main() -> None:
    async with get_write_session() as session:
        # 1) #335 shipping_status 정정 — cancelled 인데 반품/교환 라벨
        r1 = await session.execute(
            text("""
            UPDATE samba_order
            SET shipping_status = '취소완료'
            WHERE status = 'cancelled'
              AND shipping_status IN (
                  '반품요청', '교환요청', '교환회수완료', '교환재배송',
                  '교환완료', '반품완료', '반품거부'
              )
        """)
        )
        print(
            f"[#335-A] cancelled 주문 shipping_status → 취소완료 정정: {r1.rowcount}건"
        )

        # 2) #335 stale 활성 samba_return auto-close
        r2 = await session.execute(
            text("""
            UPDATE samba_return r
            SET status = 'cancelled',
                completion_detail = '취소'
            FROM samba_order o
            WHERE r.order_id = o.id
              AND o.status = 'cancelled'
              AND r.status NOT IN ('completed', 'cancelled', 'rejected')
        """)
        )
        print(f"[#335-B] 취소완료 주문 stale return auto-close: {r2.rowcount}건")

        # 3) #334 completion_detail 프론트 어휘 → 백엔드 어휘
        r3 = await session.execute(
            text("""
            UPDATE samba_return
            SET completion_detail = CASE completion_detail
                WHEN '취소완료' THEN '취소'
                WHEN '반품완료' THEN '반품'
                WHEN '교환완료' THEN '교환'
                WHEN '대기중' THEN '진행중'
                ELSE completion_detail
            END
            WHERE completion_detail IN ('취소완료', '반품완료', '교환완료', '대기중')
        """)
        )
        print(
            f"[#334] completion_detail 프론트 어휘 → 백엔드 어휘 정정: {r3.rowcount}건"
        )

        if APPLY:
            await session.commit()
            print("[COMMIT] 프로덕션 반영 완료.")
        else:
            await session.rollback()
            print("[DRY-RUN] 롤백 — 변경 없음. 실제 반영하려면 --apply 추가.")


if __name__ == "__main__":
    asyncio.run(main())
