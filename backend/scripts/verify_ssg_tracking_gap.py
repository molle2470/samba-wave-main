"""SSG 송장수집 진단 — read-only.

질문 2개 검증:
  1) SSG 송장미입력 14건 중 3건이 왜 큐(모달)에서 빠지는가
     → enqueue_pending_orders 기준으로 제외 사유 버킷팅
  2) SSG 계정 로그인 실패(성희) 원인 A/B 1차 판별
     → 해당 SSG 계정 password DB 존재 여부 (비면 A=자격증명 없음 확정)

승인/수정 호출 없음. SELECT 만.
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from backend.db.orm import get_read_session

_KST = timezone(timedelta(hours=9))
_UTC = timezone.utc

EXCLUDED_ORDER_STATUSES = (
    "cancel_requested",
    "cancelling",
    "cancelled",
    "return_requested",
    "returning",
    "returned",
    "return_completed",
    "exchange_requested",
)
SHIPPED_KEYWORDS = ("배송중", "배송완료", "구매확정", "국내배송중", "송장전송완료")


def _classify(row: dict) -> str:
    """enqueue_for_order / enqueue_pending_orders 기준으로 잡 생성 가능 여부 판정."""
    status = (row["status"] or "").lower().strip()
    ship = (row["shipping_status"] or "").strip()
    tags = f",{(row['action_tag'] or '').strip()},"
    son = (row["sourcing_order_number"] or "").strip()
    site = (row["source_site"] or "").strip()
    url = (row["source_url"] or "").strip()
    cpid = row["collected_product_id"]

    # 1) 취소 가드 (is_order_cancelled)
    if status in EXCLUDED_ORDER_STATUSES:
        return f"EXCLUDED_STATUS:{status}"
    if "취소" in ship:
        return "SHIP_취소"
    # 2) 배송 진행/완료 키워드
    for kw in SHIPPED_KEYWORDS:
        if kw in ship:
            return f"SHIPPED:{kw}"
    # 3) 소싱처 주문번호 없음
    if not son:
        return "NO_SOURCING_ORDER_NO"
    # 4) 까대기
    if ",kkadaegi," in tags:
        return "KKADAEGI"
    # 5) 소싱처 식별 불가
    if not site and not url and not cpid:
        return "NO_SOURCE_IDENTIFIABLE"
    return "ELIGIBLE(잡 생성)"


async def main() -> None:
    today_kst = datetime.now(_KST).replace(hour=0, minute=0, second=0, microsecond=0)
    since = (today_kst - timedelta(days=6)).astimezone(_UTC)
    until = (today_kst + timedelta(days=1)).astimezone(_UTC)

    async with get_read_session() as sess:
        # ── 질문 2: SSG 계정 password 존재 여부 ──
        accs = (
            await sess.execute(
                text(
                    "SELECT id, account_label, username, "
                    "       (password IS NOT NULL AND password <> '') AS has_pw, "
                    "       length(coalesce(password,'')) AS pw_len, "
                    "       is_active, is_login_default, tenant_id "
                    "FROM samba_sourcing_account "
                    "WHERE upper(site_name) = 'SSG' "
                    "ORDER BY account_label"
                )
            )
        ).mappings().all()
        print("=== [Q2] SSG 소싱처 계정 자격증명 ===")
        for a in accs:
            print(
                f"  label={a['account_label']!r} id={a['id']} "
                f"user={a['username']!r} has_pw={a['has_pw']} pw_len={a['pw_len']} "
                f"active={a['is_active']} default={a['is_login_default']}"
            )

        # ── 질문 1: 송장미입력 SSG 주문 7일치 버킷팅 ──
        rows = (
            await sess.execute(
                text(
                    "SELECT o.id, o.order_number, o.customer_name, o.status, "
                    "       o.shipping_status, o.action_tag, o.sourcing_order_number, "
                    "       o.source_site, o.source_url, o.collected_product_id, "
                    "       o.sourcing_account_id, o.tracking_number, "
                    "       coalesce(o.paid_at, o.created_at) AS d, "
                    "       sa.account_label, sa.site_name AS acc_site "
                    "FROM samba_order o "
                    "LEFT JOIN samba_sourcing_account sa ON sa.id = o.sourcing_account_id "
                    "WHERE (o.tracking_number IS NULL OR o.tracking_number = '') "
                    "  AND coalesce(o.paid_at, o.created_at) >= :since "
                    "  AND coalesce(o.paid_at, o.created_at) < :until "
                    "  AND ( upper(coalesce(o.source_site,'')) = 'SSG' "
                    "        OR upper(coalesce(sa.site_name,'')) = 'SSG' "
                    "        OR o.source_url ILIKE '%ssg.com%' ) "
                    "ORDER BY d ASC"
                ),
                {"since": since, "until": until},
            )
        ).mappings().all()

        print(
            f"\n=== [Q1] SSG 송장미입력 주문 (KST 7일 {since.astimezone(_KST):%m-%d}~"
            f"{today_kst:%m-%d}) : {len(rows)}건 ==="
        )
        buckets: dict[str, int] = {}
        for r in rows:
            reason = _classify(dict(r))
            buckets[reason] = buckets.get(reason, 0) + 1
        print("버킷:", json.dumps(buckets, ensure_ascii=False))
        print("\n상세:")
        for r in rows:
            reason = _classify(dict(r))
            mark = "OK " if reason.startswith("ELIGIBLE") else "❌ "
            print(
                f"  {mark}{reason:24s} ord_no={r['sourcing_order_number'] or '-':>18} "
                f"status={r['status']!r} ship={r['shipping_status']!r} "
                f"acc={r['account_label'] or '-'} cust={r['customer_name']}"
            )
        elig = sum(v for k, v in buckets.items() if k.startswith("ELIGIBLE"))
        print(
            f"\n요약: 총 {len(rows)}건 / 잡생성 {elig}건 / 제외 {len(rows) - elig}건"
        )


if __name__ == "__main__":
    asyncio.run(main())
