"""소싱처 주문번호로 마켓 주문번호/고객 조회 (읽기 전용)."""

import asyncio

from sqlalchemy import text

from backend.db.orm import get_read_session

SOURCING_NOS = [
    "202605232141410004",  # 무신사 병기
    "202605221407290006",  # 무신사 성희
    "20260520179F76",  # SSG 성희
    "2026051719354254",  # 롯데ON 성희
]


async def main():
    async with get_read_session() as s:
        for sno in SOURCING_NOS:
            r = (
                await s.execute(
                    text(
                        """
                        SELECT order_number, source_site, channel_name,
                               customer_name, product_name, shipping_status,
                               tracking_number, sourcing_account_id, paid_at
                        FROM samba_order
                        WHERE sourcing_order_number = :sno
                        ORDER BY paid_at DESC LIMIT 3
                        """
                    ),
                    {"sno": sno},
                )
            ).fetchall()
            print(f"\n소싱주문번호 {sno} → {len(r)}건")
            for x in r:
                print(
                    f"  마켓주문번호={x[0]} | site={x[1]} | market={x[2]} | "
                    f"고객={x[3]!r} | 상품={str(x[4])[:25]!r} | 배송={x[5]!r} | "
                    f"송장={x[6]} | acc={x[7]}"
                )


asyncio.run(main())
