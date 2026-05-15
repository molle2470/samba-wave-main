"""페이지 41건 vs 모달 32건 차이 진단.

페이지 필터: 송장 미입력 + 취소/반품/교환 제외 + 배송중/완료 제외 (최근 7일)
모달 필터: 위 + 소싱처 주문번호 있음 + source_site 있음 + 까대기 제외 + 잡 row 존재
"""

import asyncio

import asyncpg


async def main() -> None:
    from backend.core.config import settings

    conn = await asyncpg.connect(
        host=settings.write_db_host,
        port=settings.write_db_port,
        user=settings.write_db_user,
        password=settings.write_db_password,
        database=settings.write_db_name,
        ssl=False,
    )
    try:
        # 페이지와 동일 기본 필터: 최근 7일 + 송장 미입력 + 종결 상태 제외
        # (정확한 EXCLUDED_ORDER_STATUSES / SHIPPED_SHIPPING_STATUS_KEYWORDS는 코드 참조 — 간단 버전)
        rows = await conn.fetch(
            """
            SELECT
              o.id,
              o.order_number,
              o.customer_name,
              o.source_site,
              o.sourcing_order_number,
              o.action_tag,
              o.status,
              o.shipping_status,
              EXISTS (
                SELECT 1 FROM samba_tracking_sync_job j WHERE j.order_id = o.id
              ) AS has_job
            FROM samba_order o
            WHERE (o.tracking_number IS NULL OR o.tracking_number = '')
              AND o.created_at >= now() - interval '7 days'
              AND (o.status IS NULL OR o.status NOT IN ('cancelled', 'returned', 'exchanged', 'cancel_requested', 'return_requested', 'exchange_requested'))
              AND (
                o.shipping_status IS NULL
                OR (
                  o.shipping_status NOT LIKE '%배송중%'
                  AND o.shipping_status NOT LIKE '%배송완료%'
                  AND o.shipping_status NOT LIKE '%구매확정%'
                  AND o.shipping_status NOT LIKE '%정산완료%'
                  AND o.shipping_status NOT LIKE '%취소%'
                  AND o.shipping_status NOT LIKE '%반품%'
                  AND o.shipping_status NOT LIKE '%교환%'
                )
              )
            ORDER BY o.created_at DESC
            """
        )
        print(f"=== 페이지 필터 통과: {len(rows)}건 ===")
        no_srcno = []
        no_site = []
        kkadaegi = []
        no_job = []
        ok = []
        for r in rows:
            if not r["sourcing_order_number"]:
                no_srcno.append(r)
                continue
            if not r["source_site"]:
                no_site.append(r)
                continue
            tags = f",{(r['action_tag'] or '').strip()},"
            if ",kkadaegi," in tags:
                kkadaegi.append(r)
                continue
            if not r["has_job"]:
                no_job.append(r)
                continue
            ok.append(r)
        print(f"\n  [큐잉/모달 통과]  {len(ok):3d}건")
        print(f"  [소싱주문번호 없음] {len(no_srcno):3d}건 — 큐잉 불가")
        print(f"  [source_site 없음] {len(no_site):3d}건 — 큐잉 불가")
        print(f"  [까대기]           {len(kkadaegi):3d}건 — 큐잉 제외")
        print(f"  [잡 row 없음]      {len(no_job):3d}건 — 아직 큐잉 안됨")

        for label, lst in [
            ("소싱주문번호 없음", no_srcno),
            ("source_site 없음", no_site),
            ("까대기", kkadaegi),
            ("잡 row 없음", no_job),
        ]:
            if not lst:
                continue
            print(f"\n=== {label} 상세 ({len(lst)}건) ===")
            for r in lst[:20]:
                print(
                    f"  {r['order_number']:25s} {r['customer_name']:10s} "
                    f"src={r['source_site']!r:20s} src_no={r['sourcing_order_number']!r:20s} "
                    f"action_tag={r['action_tag']!r:20s} status={r['status']!r:15s} "
                    f"ship={r['shipping_status']!r}"
                )
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
