"""잔여 4건 draft 재전송 — 간헐 503/timeout 대응 재시도 루프."""

import json
import time

import requests

TOKEN = "eAjWqVyzJpsAlk8HClUHTXh97EO3_DkMiRkyBd1BdcQZSNYe"
BASE = "https://api.samba-wave.co.kr/api/v1/internal/cs"
HEADERS = {"X-Internal-Token": TOKEN, "Content-Type": "application/json"}

REMAINING = [
    ("csi_01KRXDHV2V3G41FD4PVG91VTNH", "tracking", "안녕하세요 고객님. 배송 진행 상황 확인 후 안내드리겠습니다."),
    ("csi_01KRXDHTM395R46RJ01JDEH97W", "order_change", "안녕하세요 고객님. 옵션 변경 요청 건과 취소 경위 확인 후 처리해드리겠습니다."),
    ("csi_01KP4PNEW2HYXBJ6H0X5TAN9CT", "stock_check", "안녕하세요 고객님. 90·95 사이즈 재고 확인 후 안내드리겠습니다."),
    ("csi_01KP2ZMA9YBME972AMMEHMGZXZ", "general", "안녕하세요 고객님. 좋은 후기 남겨주셔서 감사합니다."),
]


def post_with_retry(iid: str, intent: str, reply: str, max_try: int = 5) -> bool:
    body = {"inquiry_id": iid, "intent": intent, "draft_reply": reply, "confidence": 0.7, "source": "claude"}
    for attempt in range(1, max_try + 1):
        try:
            r = requests.post(f"{BASE}/draft", headers=HEADERS, data=json.dumps(body), timeout=60)
            if r.status_code == 200:
                print(f"[draft] {iid} -> 200 (attempt {attempt})")
                return True
            print(f"[draft] {iid} -> {r.status_code} (attempt {attempt}), 재시도")
        except Exception as e:
            print(f"[draft] {iid} -> EXC {type(e).__name__} (attempt {attempt}), 재시도")
        time.sleep(3)
    print(f"[draft] {iid} -> 최종 실패")
    return False


def main() -> None:
    ok = sum(int(post_with_retry(*item)) for item in REMAINING)
    print(f"\n=== 재시도 요약: {ok}/{len(REMAINING)} 성공 ===")


if __name__ == "__main__":
    main()
