"""41건 정확한 분해 — 페이지 필터 = sourcing_order_number 있음 + tracking 미입력 +
취소/반품/교환 제외 + 배송중/배송완료 제외.

까대기 15건을 분리하고, 나머지 26건 중 큐잉/모달에 안 들어가는 5건의 정체를 찾는다.
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
        # 페이지 필터: 주문번호(=sourcing_order_number) 있음 + 송장 미입력 +
        #             status NOT IN EXCLUDED + shipping_status NOT LIKE %배송중%/%배송완료%
        #             + 최근 7일 (UI 기본값 가정)
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
                SELECT 1 FROM samba_tracking_sync_job j
                WHERE j.order_id = o.id AND j.status IN ('PENDING','DISPATCHED','SCRAPED','SENT_TO_MARKET','DISPATCH_FAILED')
              ) AS has_active_job
            FROM samba_order o
            WHERE (o.tracking_number IS NULL OR o.tracking_number = '')
              AND o.sourcing_order_number IS NOT NULL
              AND o.sourcing_order_number <> ''
              AND o.created_at >= now() - interval '7 days'
              AND (o.status IS NULL OR o.status NOT IN (
                'cancel_requested','cancelling','cancelled',
                'return_requested','returning','returned','return_completed',
                'exchange_requested','exchanging','exchanged','exchange_pending','exchange_done',
                'ship_failed','undeliverable'
              ))
              AND (
                o.shipping_status IS NULL
                OR (
                  o.shipping_status NOT LIKE '%배송중%'
                  AND o.shipping_status NOT LIKE '%배송완료%'
                )
              )
            ORDER BY o.created_at DESC
            """
        )
        print(f"=== 페이지 필터 통과 (송장 미입력 + 주문번호 있음 + 종결 상태 제외): {len(rows)}건 ===\n")

        kkadaegi = []
        no_site = []
        no_job = []
        normal = []
        for r in rows:
            tags = f",{(r['action_tag'] or '').strip()},"
            if ",kkadaegi," in tags:
                kkadaegi.append(r)
                continue
            if not r["source_site"]:
                no_site.append(r)
                continue
            if not r["has_active_job"]:
                no_job.append(r)
                continue
            normal.append(r)

        print(f"  [까대기] {len(kkadaegi):3d}건")
        print(f"  [source_site 없음(까대기 외)] {len(no_site):3d}건 — 큐잉 불가")
        print(f"  [잡 row 없음 (까대기/site 외)] {len(no_job):3d}건 — 아직 큐잉 안됨")
        print(f"  [큐잉/모달 통과 — 정상] {len(normal):3d}건")
        print(f"  ─ 까대기 외 = {len(rows) - len(kkadaegi):3d}건 (사용자 기준 26 이어야)")

        if no_site:
            print(f"\n=== source_site 없음 상세 ({len(no_site)}건) ===")
            for r in no_site:
                print(
                    f"  {r['order_number']:25s} {r['customer_name']:12s} "
                    f"action={r['action_tag']!r:30s} status={r['status']!r:12s} ship={r['shipping_status']!r}"
                )
        if no_job:
            print(f"\n=== 잡 row 없음 상세 ({len(no_job)}건) ===")
            for r in no_job:
                print(
                    f"  {r['order_number']:25s} {r['customer_name']:12s} "
                    f"site={r['source_site']!r:15s} action={r['action_tag']!r:25s} "
                    f"status={r['status']!r:12s}"
                )
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
