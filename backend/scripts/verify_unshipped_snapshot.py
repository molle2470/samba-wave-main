"""미발송 스냅샷 검증 — 프로덕션 DB 직접 실행용.

1) samba_daily_unshipped_snapshot 테이블 생성 (IF NOT EXISTS, 배포 전 사전생성)
2) 엔드포인트/크론 라이브 산식으로 "현재 트레일링 7일 송장 대기수" 계산 → 모달 42 와 대조
3) 기존 대시보드 미발송(오늘 paid_at, SambaOrder 직접) 카운트도 같이 출력 (9 와 대조)

실행: docker cp 후 .venv/bin/python /tmp/verify_unshipped_snapshot.py
"""

import asyncio
import datetime as _dt

import asyncpg

from backend.core.config import settings

EXCLUDED_ORDER_STATUSES = (
    "cancel_requested",
    "cancelling",
    "cancelled",
    "return_requested",
    "returning",
    "returned",
    "return_completed",
    "exchange_requested",
    "exchanging",
    "exchanged",
    "exchange_pending",
    "exchange_done",
    "ship_failed",
    "undeliverable",
)
SHIPPED_SHIPPING_STATUS_KEYWORDS = (
    "배송중",
    "배송완료",
    "구매확정",
    "국내배송중",
    "송장전송완료",
)


async def main():
    conn = await asyncpg.connect(
        user=settings.write_db_user,
        password=settings.write_db_password,
        host=settings.write_db_host,
        port=settings.write_db_port,
        database=settings.write_db_name,
        ssl=False,
    )
    try:
        # 1) 테이블 사전생성
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS samba_daily_unshipped_snapshot (
                snapshot_date VARCHAR(10) PRIMARY KEY,
                unshipped_count INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        print("[1] 테이블 생성/존재 확인 완료")

        _KST = _dt.timezone(_dt.timedelta(hours=9))
        _now_kst = _dt.datetime.now(_KST)
        _today_kst0 = _now_kst.replace(hour=0, minute=0, second=0, microsecond=0)
        since = _today_kst0 - _dt.timedelta(days=6)  # KST 오늘-6일 00:00
        until = _today_kst0 + _dt.timedelta(days=1)  # KST 내일 00:00 (exclusive)

        # 2) 송장 수집대상 주문 수 — enqueue_pending_orders WHERE + 모달 배송키워드 제외.
        #    SambaOrder 직접(잡 존재 무관) → 결정적(순간값 아님). 모달 '대기'와 동일 대상집합.
        row = await conn.fetchrow(
            """
            SELECT COUNT(*) AS cnt FROM samba_order o
            WHERE (o.tracking_number IS NULL OR o.tracking_number = '')
              AND o.sourcing_order_number IS NOT NULL AND o.sourcing_order_number <> ''
              AND (
                    (o.source_site IS NOT NULL AND o.source_site <> '')
                 OR (o.source_url IS NOT NULL AND o.source_url <> '')
                 OR o.collected_product_id IS NOT NULL
              )
              AND COALESCE(o.paid_at, o.created_at) >= $1
              AND COALESCE(o.paid_at, o.created_at) < $2
              AND (o.status IS NULL OR o.status <> ALL($3::text[]))
              AND POSITION(',kkadaegi,' IN ',' || COALESCE(o.action_tag, '') || ',') = 0
              AND NOT (COALESCE(o.shipping_status, '') LIKE ANY($4::text[]))
            """,
            since.astimezone(_dt.timezone.utc),
            until.astimezone(_dt.timezone.utc),
            list(EXCLUDED_ORDER_STATUSES),
            [f"%{kw}%" for kw in SHIPPED_SHIPPING_STATUS_KEYWORDS],
        )
        live_target = int(row["cnt"]) if row else 0
        print(
            f"[2] 송장 수집대상 주문(트레일링 7일) = {live_target}건  (모달 '대기' 42 와 대조)"
        )

        # 3) 기존 대시보드 미발송 — 오늘(KST) paid_at 미발송 (9 와 대조)
        today_kst = _now_kst.replace(hour=0, minute=0, second=0, microsecond=0)
        row2 = await conn.fetchrow(
            """
            SELECT COUNT(*) AS cnt FROM samba_order o
            WHERE o.paid_at IS NOT NULL
              AND o.paid_at >= $1
              AND (o.tracking_number IS NULL OR o.tracking_number = '')
              AND (o.status IS NULL OR o.status <> ALL($2::text[]))
              AND NOT (COALESCE(o.shipping_status,'') LIKE ANY($3::text[]))
            """,
            today_kst,
            list(EXCLUDED_ORDER_STATUSES),
            [f"%{kw}%" for kw in SHIPPED_SHIPPING_STATUS_KEYWORDS],
        )
        old_today = int(row2["cnt"]) if row2 else 0
        print(
            f"[3] 기존 대시보드 미발송(오늘 paid_at) = {old_today}건  (기존 9 와 대조)"
        )
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
