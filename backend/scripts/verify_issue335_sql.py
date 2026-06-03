"""issue #335 신규/수정 raw SQL 프로덕션 검증 (롤백, 미커밋).

- Part B auto-close UPDATE 가 syntax 통과하고 cancelled 주문 활성 return 만 닫는지
- Part A 확장 보호목록 일괄 SQL 이 syntax 통과하는지
검증 후 전부 ROLLBACK — 프로덕션 데이터 변경 없음.
"""

import asyncio

from sqlalchemy import text

from backend.db.orm import get_write_session


async def main() -> None:
    async with get_write_session() as session:
        # 1) 현재 불일치 현황 (read-only 진단)
        diag = await session.execute(
            text("""
            SELECT COUNT(*)
            FROM samba_order o
            JOIN samba_return r ON r.order_id = o.id
            WHERE o.status = 'cancelled'
              AND r.status NOT IN ('completed', 'cancelled', 'rejected')
        """)
        )
        seed_cnt = diag.scalar()
        print(f"[진단] 취소완료 주문의 stale 활성 samba_return 건수: {seed_cnt}")

        # 2) Part B auto-close — 실제 실행 후 rowcount 확인 (커밋 안 함)
        ac = await session.execute(
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
        print(
            f"[Part B] auto-close UPDATE rowcount: {ac.rowcount} (= seed 건수 일치 기대)"
        )

        # 3) Part A 확장 일괄 SQL — syntax/실행 검증 (커밋 안 함)
        upd = await session.execute(
            text("""
            UPDATE samba_order o
            SET shipping_status = CASE
                WHEN r.type = 'exchange' THEN '교환요청'
                WHEN r.type = 'return' THEN '반품요청'
                ELSE o.shipping_status
            END
            FROM samba_return r
            WHERE r.order_id = o.id
              AND r.status NOT IN ('completed', 'cancelled', 'rejected')
              AND o.shipping_status NOT IN (
                  '교환요청', '교환회수완료', '교환재배송', '교환완료',
                  '반품요청', '반품완료', '반품거부',
                  '취소요청', '취소처리중', '취소완료',
                  '주문접수', '배송대기중', '송장전송완료', '국내배송중',
                  '배송완료', '구매확정'
              )
        """)
        )
        print(f"[Part A] 확장 일괄 SQL rowcount: {upd.rowcount} (정상 실행)")

        # 절대 커밋하지 않음 — 검증만
        await session.rollback()
        print(
            "[OK] 전부 ROLLBACK 완료. 프로덕션 변경 없음. SQL 3종 syntax/실행 검증 통과."
        )


if __name__ == "__main__":
    asyncio.run(main())
