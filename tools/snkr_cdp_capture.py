"""웨일 CDP(9223)로 트레이딩카드 used 페이지 열고 XHR(API) 캡처."""

import asyncio
import json
import sys

import httpx
import websockets


async def main(url: str):
    # 새 탭 생성
    async with httpx.AsyncClient() as hc:
        r = await hc.put(f"http://localhost:9223/json/new?{url}")
        if r.status_code >= 400:
            r = await hc.get(f"http://localhost:9223/json/new?{url}")
        tab = r.json()
        ws_url = tab["webSocketDebuggerUrl"]
        tab_id = tab["id"]
        print(f"tab={tab_id}")

    captured = []
    async with websockets.connect(ws_url, max_size=None) as ws:
        mid = 0

        async def send(method, params=None):
            nonlocal mid
            mid += 1
            await ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
            return mid

        await send("Network.enable")
        await send("Page.enable")
        await send("Page.navigate", {"url": url})

        # 8초간 네트워크 이벤트 수집
        try:
            while True:
                msg = await asyncio.wait_for(ws.recv(), timeout=8.0)
                data = json.loads(msg)
                if data.get("method") == "Network.requestWillBeSent":
                    req = data["params"]["request"]
                    u = req.get("url", "")
                    if "/v1/" in u or "api" in u.lower() or "listing" in u.lower():
                        if "trading" in u or "listing" in u or "/v1/" in u:
                            captured.append((req.get("method"), u))
        except asyncio.TimeoutError:
            pass

    print("\n=== 캡처된 API 요청 ===")
    seen = set()
    for m, u in captured:
        key = u.split("?")[0]
        if u in seen:
            continue
        seen.add(u)
        print(f"  {m} {u}")

    # 탭 닫기
    async with httpx.AsyncClient() as hc:
        await hc.get(f"http://localhost:9223/json/close/{tab_id}")


if __name__ == "__main__":
    u = (
        sys.argv[1]
        if len(sys.argv) > 1
        else "https://snkrdunk.com/en/trading-cards/671486/used?sort=latest&isOnlyOnSale=true"
    )
    asyncio.run(main(u))
