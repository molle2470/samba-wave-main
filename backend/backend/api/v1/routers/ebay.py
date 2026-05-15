"""eBay Marketplace Account Deletion Notification 엔드포인트."""

import hashlib
import logging

from fastapi import APIRouter, Query, Request, Response

from backend.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ebay"])


@router.get("/ebay/deletion-notification")
async def ebay_deletion_challenge(
    challenge_code: str = Query(..., description="eBay challenge code"),
) -> Response:
    """eBay endpoint 검증 — challenge-response 응답.

    eBay가 엔드포인트 등록 시 GET 요청으로 challenge_code를 보내면
    SHA-256(challenge_code + verification_token + endpoint_url) 해시를 반환.

    환경변수(EBAY_DELETION_NOTIFICATION_URL, EBAY_VERIFICATION_TOKEN)가
    설정되어 있지 않으면 503을 반환 — 잘못된 해시로 응답해 검증 통과가 되는 사고 방지.
    """
    token = settings.ebay_verification_token
    url = settings.ebay_deletion_notification_url
    if not token or not url:
        logger.error(
            "[eBay] EBAY_VERIFICATION_TOKEN / EBAY_DELETION_NOTIFICATION_URL "
            "환경변수 미설정 — endpoint 비활성화 상태"
        )
        return Response(
            content='{"error":"endpoint not configured"}',
            media_type="application/json",
            status_code=503,
        )

    hash_input = challenge_code + token + url
    challenge_response = hashlib.sha256(hash_input.encode("utf-8")).hexdigest()

    logger.info("[eBay] Challenge 검증 요청: challenge_code=%s", challenge_code[:10])

    return Response(
        content=f'{{"challengeResponse": "{challenge_response}"}}',
        media_type="application/json",
    )


@router.post("/ebay/deletion-notification")
async def ebay_deletion_notification(request: Request) -> dict:
    """eBay 마켓플레이스 계정 삭제 알림 수신.

    eBay 구매자가 계정 삭제 시 이 엔드포인트로 POST 요청이 옴.
    해당 구매자의 주문 정보에서 개인정보를 익명화 처리.
    """
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    logger.info("[eBay] 계정 삭제 알림 수신: %s", payload)

    # 삭제 대상 유저 정보 추출
    notification = payload.get("notification", {})
    data = notification.get("data", {})
    username = data.get("username", "")
    user_id = data.get("userId", "")

    if username or user_id:
        logger.info("[eBay] 삭제 요청 유저: username=%s, userId=%s", username, user_id)
        # TODO: samba_order 테이블에서 해당 구매자 개인정보 익명화
        # await anonymize_buyer_data(username=username, user_id=user_id)

    return {"status": "ok"}
