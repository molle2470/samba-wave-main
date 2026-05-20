"""SSG 기존 주문 product_option 백필 스크립트.

옵션이 없는 SSG 주문에 대해 /api/claim/v2/order/{orordNo} API를 호출해
uitemNm(상품 옵션명)을 가져와 업데이트한다.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def main():
    from backend.core.config import settings
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy import text

    url = (
        f"postgresql+asyncpg://{settings.write_db_user}:{settings.write_db_password}"
        f"@{settings.write_db_host}:{settings.write_db_port}/{settings.write_db_name}"
    )
    engine = create_async_engine(url, echo=False)
    AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with AsyncSessionLocal() as session:
        # 옵션 없는 SSG 주문 조회
        rows = await session.execute(
            text("""
                SELECT id, order_number, ord_prd_seq, channel_id
                FROM samba_order
                WHERE source = 'ssg'
                  AND (product_option IS NULL OR product_option = '')
                  AND order_number IS NOT NULL
                  AND order_number != ''
                ORDER BY channel_id, order_number
            """)
        )
        orders = rows.fetchall()
        print(f"옵션 없는 SSG 주문: {len(orders)}건")
        if not orders:
            return

        # 계정별 API 자격증명 조회
        account_ids = list({r.channel_id for r in orders})
        cred_rows = await session.execute(
            text("""
                SELECT id, additional_fields
                FROM samba_market_account
                WHERE id = ANY(:ids)
            """),
            {"ids": account_ids},
        )
        creds = {
            r.id: (json.loads(r.additional_fields) if isinstance(r.additional_fields, str) else r.additional_fields)
            for r in cred_rows.fetchall()
        }

    await engine.dispose()

    from backend.domain.samba.proxy.ssg import SSGClient

    # 계정별로 그룹화
    by_account: dict[str, list] = {}
    for r in orders:
        by_account.setdefault(r.channel_id, []).append(r)

    total_updated = 0

    for account_id, account_orders in by_account.items():
        af = creds.get(account_id, {})
        api_key = af.get("apiKey", "")
        site_no = af.get("storeId", "6004")
        if not api_key:
            print(f"[{account_id}] apiKey 없음 — 건너뜀")
            continue

        client = SSGClient(api_key, site_no=site_no)

        # order_number 기준으로 그룹화 (한 번 호출로 여러 행 처리)
        by_order: dict[str, list] = {}
        for r in account_orders:
            by_order.setdefault(r.order_number, []).append(r)

        print(f"\n[{account_id}] 주문 {len(by_order)}건 API 호출 시작")

        updated = 0
        failed = 0

        engine2 = create_async_engine(url, echo=False)
        AsyncSessionLocal2 = sessionmaker(engine2, class_=AsyncSession, expire_on_commit=False)

        for order_number, rows_for_order in by_order.items():
            try:
                detail_items = await client.get_order_detail(order_number)

                # ordItemSeq(ord_prd_seq) 기준으로 uitemNm 매핑
                option_map: dict[str, str] = {}
                for item in detail_items:
                    seq = str(item.get("ordItemSeq", "") or "")
                    uitem_nm = str(item.get("uitemNm", "") or "")
                    if seq and uitem_nm:
                        option_map[seq] = uitem_nm

                for row in rows_for_order:
                    ord_prd_seq = str(row.ord_prd_seq or "")
                    option_name = option_map.get(ord_prd_seq, "")
                    if not option_name:
                        # ord_prd_seq 없거나 매핑 안 되면 첫 번째 값으로 fallback
                        option_name = next(iter(option_map.values()), "")

                    if option_name:
                        async with AsyncSessionLocal2() as session2:
                            await session2.execute(
                                text("UPDATE samba_order SET product_option = :opt WHERE id = :id"),
                                {"opt": option_name, "id": row.id},
                            )
                            await session2.commit()
                        updated += 1

            except Exception as e:
                print(f"  [{order_number}] 실패: {e}")
                failed += 1

            await asyncio.sleep(0.2)  # API 레이트리밋 방지

        await engine2.dispose()
        total_updated += updated
        print(f"[{account_id}] 완료 - 업데이트 {updated}건, 실패 {failed}건")

    print(f"\n전체 완료 - 총 {total_updated}건 업데이트")


if __name__ == "__main__":
    asyncio.run(main())
