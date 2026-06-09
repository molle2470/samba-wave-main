"""스케줄 작업 cs_response — 작성한 답변 초안/자동전송 일괄 전송.

한글 본문 안전 전송을 위해 requests + json 사용.
"""

import json

import requests

TOKEN = "eAjWqVyzJpsAlk8HClUHTXh97EO3_DkMiRkyBd1BdcQZSNYe"
BASE = "https://api.samba-wave.co.kr/api/v1/internal/cs"
HEADERS = {"X-Internal-Token": TOKEN, "Content-Type": "application/json"}

# 자동전송 대상 (notice_ack, auto_send_eligible=true) — POST /auto-send
AUTO_SEND = [
    ("csi_01KTKHH4MFRBQPA638DBNYP4KD", "안녕하세요 고객님. 안내해주신 내용 확인했습니다."),
    ("csi_01KTKHG6EFD8Q7RV48Q7Q3YB34", "안녕하세요 고객님. 안내해주신 내용 확인했습니다."),
    ("csi_01KTJCJA80N952Y2QQ3PTJYY0R", "안녕하세요 고객님. 안내해주신 내용 확인했습니다."),
    ("csi_01KTKHH6S4JMN0W6KB326VVP8Z", "안녕하세요 고객님. 안내해주신 내용 확인했습니다."),
    ("csi_01KTKHGAB7XJ4EZ6C4SGA25W0X", "안녕하세요 고객님. 안내해주신 내용 확인했습니다."),
    ("csi_01KTKHH8Y2SZAR72GPC67TKHTA", "안녕하세요 고객님. 안내해주신 내용 확인했습니다."),
]

# 초안 저장 대상 (auto_send_eligible=false) — POST /draft
# (inquiry_id, intent, draft_reply)
DRAFTS = [
    ("csi_01KRXDHXDWVY4FZKRCYCKJX2JR", "exchange_return", "안녕하세요 고객님. 수거 및 입고 여부 확인 후 빠르게 환불 처리해드리겠습니다."),
    ("csi_01KRXDHX3T93YBTCNV8HTT86AG", "exchange_return", "안녕하세요 고객님. 배송된 색상 관련 사항 확인 후 처리 및 안내드리겠습니다."),
    ("csi_01KRXDHWX3WFH9YP0Y4G2K44Z4", "general", "안녕하세요 고객님. 선물포장 가능 여부 확인 후 안내드리겠습니다."),
    ("csi_01KRXDHWB7RGWAYXNJ7NCHM953", "delivery_eta", "안녕하세요 고객님. 일반 발주 후 영업일 3일 내외 배송되며, 픽업 서비스 가능 여부는 확인 후 안내드리겠습니다."),
    ("csi_01KRXDHV2V3G41FD4PVG91VTNH", "tracking", "안녕하세요 고객님. 배송 진행 상황 확인 후 안내드리겠습니다."),
    ("csi_01KRXDHTM395R46RJ01JDEH97W", "order_change", "안녕하세요 고객님. 옵션 변경 요청 건과 취소 경위 확인 후 처리해드리겠습니다."),
    ("csi_01KRXDHT7TPSY1366ET5G7T8KZ", "exchange_return", "안녕하세요 고객님. 반품 철회 내용 확인했습니다. 재문의 시 다시 안내드리겠습니다."),
    ("csi_01KRXDHRY78F9TYBVAFRX880BW", "tracking", "안녕하세요 고객님. 수거 재접수 송장 확인 후 회신드리겠습니다."),
    ("csi_01KRXDHS9PVBV6CNSWPVT01SM0", "general", "안녕하세요 고객님. 수거 진행 내용 확인했습니다. 입고 후 처리해드리겠습니다."),
    ("csi_01KRXDHSRDTNVWK9QVN4D62RE8", "tracking", "안녕하세요 고객님. 수거 진행 여부 및 송장 확인 후 안내드리겠습니다."),
    ("csi_01KRXDHRFRFKHCCFJ7KJWFFVT2", "order_change", "안녕하세요 고객님. 취소 후 배송된 상품 수거 접수 진행하겠습니다."),
    ("csi_01KRXDHR0MBTGFM4VNFRQQXXD0", "stock_check", "안녕하세요 고객님. 수거 송장 및 입고 여부 확인 후 안내드리겠습니다."),
    ("csi_01KPW5BAM4QKF8NJEY3DHHKRR8", "general", "안녕하세요 고객님. 해당 상품 정보 확인 후 안내드리겠습니다."),
    ("csi_01KPW5BAKNMFKHM6413QKXW3A5", "general", "안녕하세요 고객님. 정식 유통 상품 여부 확인 후 안내드리겠습니다."),
    ("csi_01KPW5BAJX2MDTNCDR1N0GA8YE", "exchange_return", "안녕하세요 고객님. 요청하신 가격 조정 및 재결제 가능 여부 확인 후 안내드리겠습니다."),
    ("csi_01KPW5BAJEWYNRTY3N3AA8J282", "delivery_eta", "안녕하세요 고객님. 배송 분실 관련 사항 확인 후 빠르게 처리 및 안내드리겠습니다."),
    ("csi_01KP864ES67E4QZJ68YMP86AH7", "general", "안녕하세요 고객님. 문의 내용 확인 후 안내드리겠습니다."),
    ("csi_01KP4PNEX91K0XA7RBDTMJFJ0D", "stock_check", "안녕하세요 고객님. 동일 색상 주문 가능 옵션 확인 후 안내드리겠습니다."),
    ("csi_01KP4PNEW2HYXBJ6H0X5TAN9CT", "stock_check", "안녕하세요 고객님. 90·95 사이즈 재고 확인 후 안내드리겠습니다."),
    ("csi_01KP2ZMA9YBME972AMMEHMGZXZ", "general", "안녕하세요 고객님. 좋은 후기 남겨주셔서 감사합니다."),
    ("csi_01KP2ZM9QVC3QC9V4NT7DW5N37", "delivery_eta", "안녕하세요 고객님. 좋은 후기 남겨주셔서 감사합니다."),
    ("csi_01KP2ZM9RQWD37PK9JTEBF47Z2", "exchange_return", "안녕하세요 고객님. 상품 상이 관련 사항 확인 후 교환 처리 및 안내드리겠습니다."),
    ("csi_01KP2ZM9TDZ8A62J8P7JA5H65G", "general", "안녕하세요 고객님. 좋은 후기 남겨주셔서 감사합니다."),
    ("csi_01KP2ZM9VCQX8WG5RKD4BP59Y5", "sizing", "안녕하세요 고객님. 좋은 후기 남겨주셔서 감사합니다."),
]


def main() -> None:
    auto_ok = 0
    draft_ok = 0

    for iid, reply in AUTO_SEND:
        body = {"inquiry_id": iid, "draft_reply": reply, "confidence": 0.9, "dry_run": False}
        r = requests.post(f"{BASE}/auto-send", headers=HEADERS, data=json.dumps(body), timeout=30)
        ok = r.status_code == 200
        auto_ok += int(ok)
        print(f"[auto-send] {iid} -> {r.status_code} {r.text[:200]}")

    for iid, intent, reply in DRAFTS:
        body = {"inquiry_id": iid, "intent": intent, "draft_reply": reply, "confidence": 0.7, "source": "claude"}
        r = requests.post(f"{BASE}/draft", headers=HEADERS, data=json.dumps(body), timeout=30)
        ok = r.status_code == 200
        draft_ok += int(ok)
        print(f"[draft] {iid} -> {r.status_code} {r.text[:200]}")

    print(f"\n=== 요약: auto-send {auto_ok}/{len(AUTO_SEND)} 성공, draft {draft_ok}/{len(DRAFTS)} 성공 ===")


if __name__ == "__main__":
    main()
