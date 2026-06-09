"""notice_ack 6건 초안 문구 정정 — '고객님' 제거, 셀러→11번가 맥락 회신.

서버 간헐 503 대응 재시도 루프 포함.
auto_send_eligible=true 건이므로 /auto-send 로 재호출(킬스위치 OFF → 초안만 갱신).
"""

import json
import time

import requests

TOKEN = "eAjWqVyzJpsAlk8HClUHTXh97EO3_DkMiRkyBd1BdcQZSNYe"
BASE = "https://api.samba-wave.co.kr/api/v1/internal/cs"
HEADERS = {"X-Internal-Token": TOKEN, "Content-Type": "application/json"}

PENALTY = "안내 확인했습니다. 주문이행 및 발송일 준수를 개선하여 판매자 평점을 회복하겠습니다."
SURVEY = "안내 확인했습니다."
SHIP_DELAY = "안내 확인했습니다. 미발송 건 신속히 발송 처리하겠습니다."

NOTICE_ACK = [
    ("csi_01KTKHH4MFRBQPA638DBNYP4KD", PENALTY),   # 고경 판매자평점 패널티
    ("csi_01KTKHG6EFD8Q7RV48Q7Q3YB34", PENALTY),   # 무놀 판매자평점 패널티
    ("csi_01KTJCJA80N952Y2QQ3PTJYY0R", PENALTY),   # 가디 판매자평점 패널티
    ("csi_01KTKHH6S4JMN0W6KB326VVP8Z", SURVEY),    # AI 셀링툴 설문
    ("csi_01KTKHGAB7XJ4EZ6C4SGA25W0X", SURVEY),    # AI 셀링툴 설문
    ("csi_01KTKHH8Y2SZAR72GPC67TKHTA", SHIP_DELAY),  # 발송지연 노출제한
]


def post_with_retry(iid: str, reply: str, max_try: int = 5) -> bool:
    body = {"inquiry_id": iid, "draft_reply": reply, "confidence": 0.9, "dry_run": False}
    for attempt in range(1, max_try + 1):
        try:
            r = requests.post(f"{BASE}/auto-send", headers=HEADERS, data=json.dumps(body), timeout=60)
            if r.status_code == 200:
                print(f"[auto-send] {iid} -> 200 (attempt {attempt}) {r.text[:120]}")
                return True
            print(f"[auto-send] {iid} -> {r.status_code} (attempt {attempt}), 재시도")
        except Exception as e:
            print(f"[auto-send] {iid} -> EXC {type(e).__name__} (attempt {attempt}), 재시도")
        time.sleep(3)
    print(f"[auto-send] {iid} -> 최종 실패")
    return False


def main() -> None:
    ok = sum(int(post_with_retry(iid, reply)) for iid, reply in NOTICE_ACK)
    print(f"\n=== notice_ack 정정 요약: {ok}/{len(NOTICE_ACK)} 성공 ===")


if __name__ == "__main__":
    main()
