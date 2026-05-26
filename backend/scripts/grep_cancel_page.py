"""order-cancel.page chunk에서 POST 호출 + body shape 추출."""

import re
from urllib.request import Request, urlopen

# 페이지에서 본 chunk 이름 — 빌드해시는 변할 수 있어 fallback 패턴 시도
chunks = [
    "https://static.msscdn.net/static/v2/order/order-cancel.page.4a1e81c6.js",
    "https://static.msscdn.net/static/v2/order/order.js",
]

for u in chunks:
    try:
        req = Request(u, headers={"User-Agent": "Mozilla/5.0"})
        data = urlopen(req, timeout=30).read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"FAIL {u}: {e}")
        continue
    print(f"\n========== {u[-60:]} ({len(data)} bytes) ==========")
    # endpoint 변수 정의 → 변수명 추출
    var_paths = {}
    for m in re.finditer(r'(\w+)\s*=\s*"(/api2/claim/store/mypage/order/(?:shipping/)?cancel/:orderNo/:orderOptionNo)"', data):
        var_paths[m.group(1)] = m.group(2)
    print("path vars:", var_paths)
    # post* function calling these
    for var, path in var_paths.items():
        for m in re.finditer(r'(post\w+)\s*=[^;]{0,300}path:\s*' + var, data):
            print(f"  POST fn: {m.group(1)} → {path}")
    # body shape — find postShippingCancel / postOrderCancel patterns + their body keys
    for name in ["postApi2ClaimStoreMypageOrderShippingCancel", "postApi2ClaimStoreMypageOrderCancel"]:
        idx = data.find(name)
        if idx > 0:
            print(f"\n--- {name} context ---")
            print(data[max(0,idx-200):idx+800])
    # claimReasonCode/refundAccount/refundBank usage
    for kw in ["claimReasonCode", "refundAccount", "refundBank", "refundDepositor"]:
        for m in re.finditer(r'\b' + kw + r'\b[^,;\}]{0,80}', data):
            ctx = m.group(0)
            if len(ctx) > 10:
                print(f"  {kw}: ...{ctx[:120]}")
                break  # first only per kw
