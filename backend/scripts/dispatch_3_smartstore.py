"""스마트스토어 3건 송장 마켓 전송 (1회성).

대상: SambaTrackingSyncJob.status='SCRAPED' AND order.shipping_status='발송대기'
       AND market_type='smartstore'

흐름:
  1) job + order + market_account 조회
  2) SmartStoreClient.ship_product_order 호출
  3) 성공 시 job.status=SENT_TO_MARKET, order.shipping_status='송장전송완료'
     실패 시 job.last_error 에 사유 저장 (status는 SCRAPED 유지 — 재시도 가능)
"""

import asyncio
from datetime import datetime, timezone


async def main() -> None:
    from sqlalchemy import select
    from backend.db.orm import get_write_session
    from backend.domain.samba.account.model import SambaMarketAccount
    from backend.domain.samba.forbidden.repository import SambaSettingsRepository
    from backend.domain.samba.order.model import SambaOrder
    from backend.domain.samba.proxy.smartstore import SmartStoreClient
    from backend.domain.samba.tracking_sync.model import SambaTrackingSyncJob

    target_order_numbers = [
        "2026051118432591",
        "2026051249224891",
        "2026051261086141",
    ]

    async with get_write_session() as session:
        for order_number in target_order_numbers:
            print(f"\n=== {order_number} ===")

            # 주문 조회
            order = (
                await session.execute(
                    select(SambaOrder).where(SambaOrder.order_number == order_number)
                )
            ).scalars().first()
            if not order:
                print("  ❌ 주문 없음")
                continue
            print(
                f"  order_id={order.id} 송장={order.shipping_company}/{order.tracking_number}"
            )

            # 잡 조회 (SCRAPED 최신 1건)
            job = (
                await session.execute(
                    select(SambaTrackingSyncJob)
                    .where(
                        SambaTrackingSyncJob.order_id == order.id,
                        SambaTrackingSyncJob.status == "SCRAPED",
                    )
                    .order_by(SambaTrackingSyncJob.updated_at.desc())
                    .limit(1)
                )
            ).scalars().first()
            if not job:
                print("  ❌ SCRAPED 잡 없음")
                continue
            print(f"  job_id={job.id}")

            # 계정 조회
            if not order.channel_id:
                print("  ❌ channel_id 없음")
                continue
            account = await session.get(SambaMarketAccount, order.channel_id)
            if not account or account.market_type != "smartstore":
                print(f"  ❌ 스마트스토어 계정 아님: {account.market_type if account else None}")
                continue

            # API key 조회 — 계정 → settings 폴백 (ship_order 엔드포인트와 동일)
            extras = account.additional_fields or {}
            client_id = extras.get("clientId", "") or account.api_key or ""
            client_secret = (
                extras.get("clientSecret", "") or account.api_secret or ""
            )
            if not client_id or not client_secret:
                _repo = SambaSettingsRepository(session)
                _row = await _repo.find_by_async(key="store_smartstore")
                if _row and isinstance(_row.value, dict):
                    client_id = client_id or _row.value.get("clientId", "")
                    client_secret = client_secret or _row.value.get("clientSecret", "")
            if not client_id or not client_secret:
                print("  ❌ 스마트스토어 인증정보 없음")
                continue

            # 송장 전송
            try:
                client = SmartStoreClient(client_id, client_secret)
                api_resp = await client.ship_product_order(
                    order.order_number,
                    order.shipping_company or "",
                    order.tracking_number or "",
                )
                print(f"  ✅ 전송 성공: {str(api_resp)[:200]}")
                job.status = "SENT_TO_MARKET"
                job.dispatched_to_market_at = datetime.now(timezone.utc)
                job.dispatch_result = {"success": True, "api": str(api_resp)[:1000]}
                order.shipping_status = "송장전송완료"
                session.add(job)
                session.add(order)
                await session.commit()
            except Exception as exc:
                print(f"  ❌ 전송 실패: {exc}")
                job.last_error = f"dispatch 실패: {exc}"[:500]
                session.add(job)
                await session.commit()


if __name__ == "__main__":
    asyncio.run(main())
