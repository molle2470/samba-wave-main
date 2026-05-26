"""order.js chunk에서 cancel POST 호출 코드 컨텍스트 추출."""

import re
from urllib.request import Request, urlopen

URL = "https://static.msscdn.net/static/v2/order/order.js"
req = Request(URL, headers={"User-Agent": "Mozilla/5.0"})
data = urlopen(req, timeout=30).read().decode("utf-8", errors="replace")
print(f"size={len(data)}")

# 1) cancel endpoint 호출 컨텍스트 (앞뒤 600자)
for needle in [
    "/api2/claim/store/mypage/order/shipping/cancel/",
    "/api2/claim/store/mypage/order/cancel/",
    "shipping/cancel",
    "order/cancel/",
]:
    for m in re.finditer(re.escape(needle), data):
        s = max(0, m.start() - 600)
        e = min(len(data), m.end() + 800)
        print(f"\n========== '{needle}' @{m.start()} ==========")
        print(data[s:e])
        print("==========\n")
        break  # 1개씩만
