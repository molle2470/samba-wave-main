"""페이지 42건 vs 모달 큐잉 30건 차이 + 5 PENDING 비처리 원인 진단."""

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
        # 페이지 필터 = 송장 미입력 + 종결 상태 제외 + 까대기 제외 +
        #              주문번호(sourcing_order_number) 있음 + paid_at KST 5/8~5/14
        rows = await conn.fetch(
            """
            WITH kst_range AS (
              SELECT
                ((date_trunc('day', now() AT TIME ZONE 'Asia/Seoul') - interval '6 days')
                  AT TIME ZONE 'Asia/Seoul') AS s,
                ((date_trunc('day', now() AT TIME ZONE 'Asia/Seoul') + interval '1 day')
                  AT TIME ZONE 'Asia/Seoul') AS e
            )
            SELECT
              o.id,
              o.order_number,
              o.customer_name,
              o.source_site,
              o.sourcing_order_number,
              o.action_tag,
              o.status,
              o.shipping_status,
              (SELECT j.status FROM samba_tracking_sync_job j
                 WHERE j.order_id = o.id
                 ORDER BY j.updated_at DESC LIMIT 1) AS last_job_status,
              (SELECT j.created_at FROM samba_tracking_sync_job j
                 WHERE j.order_id = o.id
                 ORDER BY j.updated_at DESC LIMIT 1) AS last_job_created
            FROM samba_order o, kst_range
            WHERE (o.tracking_number IS NULL OR o.tracking_number = '')
              AND COALESCE(o.paid_at, o.created_at) >= kst_range.s
              AND COALESCE(o.paid_at, o.created_at) < kst_range.e
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
              AND (
                ',' || COALESCE(o.action_tag, '') || ',' NOT LIKE '%,kkadaegi,%'
              )
              AND o.sourcing_order_number IS NOT NULL
              AND o.sourcing_order_number <> ''
            ORDER BY o.created_at DESC
            """
        )
        print(f"=== 페이지 기준(까대기 제외 후) 적재 대상: {len(rows)}건 ===\n")

        no_site = []
        has_job_ok = []
        no_job = []
        job_skip = []
        for r in rows:
            if not r["source_site"]:
                no_site.append(r)
                continue
            ljs = r["last_job_status"]
            if ljs is None:
                no_job.append(r)
            elif ljs in ("SENT_TO_MARKET",):
                job_skip.append(r)
            else:
                has_job_ok.append(r)
        print(f"  [source_site 없음] {len(no_site):3d}건 — 큐잉 시 거부")
        print(f"  [잡 이미 있음, 정상] {len(has_job_ok):3d}건")
        print(f"  [잡 SENT_TO_MARKET (이미 마켓전송)] {len(job_skip):3d}건")
        print(f"  [잡 없음 (큐잉되어야 함)] {len(no_job):3d}건")

        if no_site:
            print(f"\n=== source_site 없음 (큐잉 거부) ===")
            for r in no_site[:30]:
                print(
                    f"  {r['order_number']:25s} action={r['action_tag']!r:30s} "
                    f"status={r['status']!r:15s}"
                )

        if no_job:
            print(f"\n=== 잡 row 없음 (왜 안 큐잉됐는지) ===")
            for r in no_job[:30]:
                print(
                    f"  {r['order_number']:25s} site={r['source_site']!r:15s} "
                    f"action={r['action_tag']!r:30s} status={r['status']!r:15s}"
                )

        # 현재 활성 잡 (live, 처리 대기) 진단
        print("\n\n=== 현재 처리 대기 PENDING 잡 (sourcing_queue live) ===")
        live = await conn.fetch(
            """
            SELECT
              j.id,
              o.order_number,
              j.owner_device_id,
              sq.payload->>'site' AS site,
              sq.created_at AS sq_created,
              sq.expires_at,
              now() AS now_ts
            FROM samba_tracking_sync_job j
            JOIN samba_order o ON o.id = j.order_id
            LEFT JOIN samba_sourcing_job sq ON sq.request_id = j.request_id
            WHERE j.status = 'PENDING'
              AND sq.status = 'pending'
              AND sq.expires_at > now()
            ORDER BY sq.created_at ASC
            """
        )
        for r in live:
            print(
                f"  {r['order_number']:25s} site={r['site']!r:12s} owner={r['owner_device_id']!r:25s} "
                f"sq_created={r['sq_created']!s:30s} expires={r['expires_at']!s}"
            )
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
