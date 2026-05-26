"""송장 데몬 전환 검증용 — 5사 발송완료 주문 샘플 추출 (읽기 전용).

각 사이트별로 tracking_number 가 이미 채워진(=정답 보유) 최근 주문을 뽑아
sourcing_order_number / sourcing_account_id / 계정 username / ord_opt_no(무신사) /
shipping_company / tracking_number 를 출력한다.
웨일(9223)에서 이 주문들의 송장조회 URL 을 열어 스크랩 JS 가 동일 송장을 뽑는지 검증.
"""

import asyncio

from sqlalchemy import text

from backend.db.orm import get_read_session

SITES = ["MUSINSA", "LOTTEON", "SSG", "ABCMART", "GRANDSTAGE"]


async def main():
    async with get_read_session() as s:
        for site in SITES:
            rows = (
                await s.execute(
                    text(
                        """
                        SELECT o.sourcing_order_number,
                               o.sourcing_account_id,
                               sa.username,
                               sa.account_label,
                               o.musinsa_ord_opt_no,
                               o.shipping_company,
                               o.tracking_number,
                               o.shipping_status,
                               o.paid_at
                        FROM samba_order o
                        LEFT JOIN samba_sourcing_account sa
                               ON sa.id = o.sourcing_account_id
                        WHERE upper(coalesce(o.source_site,'')) = :site
                          AND coalesce(o.tracking_number,'') <> ''
                          AND coalesce(o.sourcing_order_number,'') <> ''
                        ORDER BY o.paid_at DESC NULLS LAST
                        LIMIT 5
                        """
                    ),
                    {"site": site},
                )
            ).fetchall()
            print(f"\n===== {site} ({len(rows)}건) =====")
            for r in rows:
                print(
                    f"  ord={r[0]} | acc_id={r[1]} | user={r[2]!r}/{r[3]!r} | "
                    f"opt={r[4]} | courier={r[5]!r} | track={r[6]!r} | "
                    f"ship={r[7]!r} | paid={r[8]}"
                )
            if not rows:
                # 정답 송장 없는 경우 — 발송완료 단계라도 송장 미수집 주문 있나 확인
                cnt = (
                    await s.execute(
                        text(
                            "SELECT count(*) FROM samba_order "
                            "WHERE upper(coalesce(source_site,''))=:site "
                            "AND coalesce(sourcing_order_number,'')<>''"
                        ),
                        {"site": site},
                    )
                ).scalar()
                print(f"  (정답 송장 보유 0건 — 해당 소싱처 주문 총 {cnt}건)")


asyncio.run(main())
