"""알리고 SMS/카카오 알림 관련 엔드포인트."""

from __future__ import annotations

from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel.ext.asyncio.session import AsyncSession

from backend.db.orm import get_read_session_dependency, get_write_session_dependency
from backend.domain.samba.message_log.model import MessageLog
from backend.domain.samba.message_log.repository import MessageLogRepository
from backend.domain.samba.tenant.middleware import get_optional_tenant_id
from backend.utils.logger import logger

from ._helpers import _get_setting

router = APIRouter(tags=["samba-proxy"])


# ═══════════════════════════════════════════════
# 알리고 (Aligo) SMS 잔여건수 조회
# ═══════════════════════════════════════════════


@router.post("/aligo/remain")
async def aligo_remain(
    session: AsyncSession = Depends(get_read_session_dependency),
    tenant_id: Optional[str] = Depends(get_optional_tenant_id),
) -> dict[str, Any]:
    """알리고 SMS 잔여건수 조회."""
    creds = await _get_setting(session, "aligo_sms", tenant_id)
    if not creds or not isinstance(creds, dict):
        return {"success": False, "message": "SMS 설정이 저장되지 않았습니다."}

    api_key = creds.get("apiKey", "")
    user_id = creds.get("userId", "")
    if not api_key or not user_id:
        return {"success": False, "message": "API Key 또는 Identifier가 비어있습니다."}

    try:
        async with httpx.AsyncClient(timeout=15, verify=True) as client:
            resp = await client.post(
                "https://apis.aligo.in/remain/",
                data={"key": api_key, "user_id": user_id},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            data = resp.json()
            if data.get("result_code") == 1 or str(data.get("result_code")) == "1":
                return {
                    "success": True,
                    "message": "인증 성공",
                    "SMS_CNT": data.get("SMS_CNT", 0),
                    "LMS_CNT": data.get("LMS_CNT", 0),
                    "MMS_CNT": data.get("MMS_CNT", 0),
                }
            else:
                return {
                    "success": False,
                    "message": data.get("message", "알리고 API 인증 실패"),
                }
    except Exception as exc:
        logger.error(f"[알리고] 잔여건수 조회 실패: {exc}")
        return {"success": False, "message": f"알리고 API 호출 실패: {exc}"}


# ═══════════════════════════════════════════════
# 알리고 SMS 발송
# ═══════════════════════════════════════════════


class SmsRequest(BaseModel):
    receiver: str
    message: str
    title: str = ""
    order_id: Optional[str] = None
    template_raw: Optional[str] = None


@router.post("/aligo/send-sms")
async def aligo_send_sms(
    body: SmsRequest,
    session: AsyncSession = Depends(get_write_session_dependency),
    tenant_id: Optional[str] = Depends(get_optional_tenant_id),
) -> dict[str, Any]:
    """알리고 SMS/LMS 발송."""
    creds = await _get_setting(session, "aligo_sms", tenant_id)
    if not creds or not isinstance(creds, dict):
        return {"success": False, "message": "SMS 설정이 저장되지 않았습니다."}

    api_key = creds.get("apiKey", "")
    user_id = creds.get("userId", "")
    sender = creds.get("sender", "")
    if not api_key or not user_id or not sender:
        return {
            "success": False,
            "message": "SMS 설정이 불완전합니다 (apiKey/userId/sender 필요).",
        }

    msg_bytes = len(body.message.encode("euc-kr", errors="replace"))
    is_lms = msg_bytes > 90

    data = {
        "key": api_key,
        "user_id": user_id,
        "sender": sender,
        "receiver": body.receiver.replace("-", ""),
        "msg": body.message,
    }
    if is_lms and body.title:
        data["title"] = body.title

    url = "https://apis.aligo.in/send/"
    success = False
    msg_id = None
    result_msg = ""

    try:
        async with httpx.AsyncClient(timeout=15, verify=True) as client:
            resp = await client.post(
                url,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            result = resp.json()
            if result.get("result_code") == 1 or str(result.get("result_code")) == "1":
                success = True
                msg_id = str(result.get("msg_id", ""))
                result_msg = f"{'LMS' if is_lms else 'SMS'} 발송 성공"
            else:
                result_msg = result.get("message", "발송 실패")
    except Exception as exc:
        logger.error(f"[알리고] SMS 발송 실패: {exc}")
        result_msg = f"SMS 발송 실패: {exc}"

    # 발송 이력 저장 (성공/실패 모두)
    try:
        repo = MessageLogRepository(session)
        await repo.create(
            MessageLog(
                tenant_id=tenant_id,
                order_id=body.order_id,
                customer_phone=body.receiver,
                message_type="sms",
                template_raw=body.template_raw,
                rendered_message=body.message,
                receiver=body.receiver.replace("-", ""),
                success=success,
                result_message=result_msg,
                msg_id=msg_id,
            )
        )
    except Exception as exc:
        logger.error(f"[알리고] SMS 이력 저장 실패: {exc}")

    if success:
        return {
            "success": True,
            "message": result_msg,
            "msg_id": msg_id,
            "msg_type": "LMS" if is_lms else "SMS",
        }
    return {"success": False, "message": result_msg}


# ═══════════════════════════════════════════════
# 알리고 카카오 알림톡 발송
# ═══════════════════════════════════════════════


class KakaoRequest(BaseModel):
    receiver: str
    message: str
    template_code: str = ""
    subject: str = ""
    order_id: Optional[str] = None
    template_raw: Optional[str] = None


@router.post("/aligo/send-kakao")
async def aligo_send_kakao(
    body: KakaoRequest,
    session: AsyncSession = Depends(get_write_session_dependency),
    tenant_id: Optional[str] = Depends(get_optional_tenant_id),
) -> dict[str, Any]:
    """알리고 카카오 알림톡/친구톡 발송."""
    creds = await _get_setting(session, "aligo_sms", tenant_id)
    if not creds or not isinstance(creds, dict):
        return {"success": False, "message": "SMS 설정이 저장되지 않았습니다."}

    kakao_creds = await _get_setting(session, "aligo_kakao", tenant_id)

    api_key = creds.get("apiKey", "")
    user_id = creds.get("userId", "")
    sender = creds.get("sender", "")
    sender_key = (
        (kakao_creds or {}).get("senderKey", "")
        if isinstance(kakao_creds, dict)
        else ""
    )

    if not api_key or not user_id:
        return {"success": False, "message": "SMS 설정이 불완전합니다."}
    if not sender_key:
        return {
            "success": False,
            "message": "카카오 발신프로필 키(senderKey)가 설정되지 않았습니다. 설정 페이지에서 등록해주세요.",
        }

    data = {
        "key": api_key,
        "user_id": user_id,
        "sender": sender,
        "receiver_1": body.receiver.replace("-", ""),
        "message_1": body.message,
        "senderkey": sender_key,
        "tpl_code": body.template_code,
    }
    if body.subject:
        data["subject_1"] = body.subject

    url = (
        "https://kakaoapi.aligo.in/akv10/alimtalk/send/"
        if body.template_code
        else "https://kakaoapi.aligo.in/akv10/friendtalk/send/"
    )

    success = False
    result_msg = ""

    try:
        async with httpx.AsyncClient(timeout=15, verify=True) as client:
            resp = await client.post(
                url,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            result = resp.json()
            if result.get("code") == 0 or str(result.get("code")) == "0":
                success = True
                result_msg = "카카오톡 발송 성공"
            else:
                result_msg = result.get("message", "카카오 발송 실패")
    except Exception as exc:
        logger.error(f"[알리고] 카카오 발송 실패: {exc}")
        result_msg = f"카카오 발송 실패: {exc}"

    # 발송 이력 저장 (성공/실패 모두)
    try:
        repo = MessageLogRepository(session)
        await repo.create(
            MessageLog(
                tenant_id=tenant_id,
                order_id=body.order_id,
                customer_phone=body.receiver,
                message_type="kakao",
                template_raw=body.template_raw,
                rendered_message=body.message,
                receiver=body.receiver.replace("-", ""),
                success=success,
                result_message=result_msg,
            )
        )
    except Exception as exc:
        logger.error(f"[알리고] 카카오 이력 저장 실패: {exc}")

    if success:
        return {
            "success": True,
            "message": result_msg,
            "msg_type": "알림톡" if body.template_code else "친구톡",
        }
    return {"success": False, "message": result_msg}
