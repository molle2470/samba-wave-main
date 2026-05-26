"""LOTTEON processOrderCancellation body 형식 추출."""

import re
from urllib.request import Request, urlopen

URL = "https://static.lotteon.com/p/order/mylotte/orderDeliveryList/assets/js/main.7b596b67.js"
data = urlopen(Request(URL, headers={"User-Agent":"Mozilla/5.0"}), timeout=20).read().decode("utf-8", errors="replace")
print(f"size={len(data)}")

# processOrderCancellation 호출 컨텍스트
for needle in ["processOrderCancellation", "computeCancellationAmountInfo", "withdrawOrderCancellation"]:
    for m in re.finditer(re.escape(needle), data):
        s = max(0, m.start() - 400)
        e = min(len(data), m.end() + 800)
        print(f"\n========== {needle} @{m.start()} ==========")
        print(data[s:e])
        print("==========")
        break  # 1개씩
