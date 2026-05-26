"""실제 SnkrdunkClient.get_detail 파싱 결과 확인."""

import asyncio

from backend.domain.samba.proxy.snkrdunk import SnkrdunkClient


async def main():
    c = SnkrdunkClient()
    for sid, t in [("IQ8055-100", "sneaker"), ("815336", "streetwear")]:
        r = await c.get_detail(sid, t)
        print(f"=== {sid} {t} ===")
        print("  err:", r.get("error"))
        print("  sale_price:", r.get("sale_price"))
        print("  original_price:", r.get("original_price"))
        print("  sale_status:", r.get("sale_status"))
        opts = r.get("options") or []
        print("  options_n:", len(opts))
        print("  options[:3]:", opts[:3])


asyncio.run(main())
