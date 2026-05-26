"""autotune/filters available_* 캐시 워밍업 — 컨테이너 재시작 후 첫 호출 100초 회피."""

import asyncio


async def main():
    from backend.api.v1.routers.samba.collector_autotune import autotune_get_filters

    print("autotune_get_filters 호출 시작 (cold cache, 최대 100초+)...")
    res = await autotune_get_filters()
    print(
        f"완료. sources={len(res.get('available_sources') or [])}건, markets={len(res.get('available_markets') or [])}건"
    )
    print(f"sources={res.get('available_sources')}")
    print(f"markets={res.get('available_markets')}")


asyncio.run(main())
