"""기타 마켓 인증 테스트 엔드포인트 (11st, 쿠팡, 롯데ON, SSG, 범용)."""

from __future__ import annotations

from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel.ext.asyncio.session import AsyncSession

from backend.db.orm import get_read_session_dependency
from backend.domain.samba.proxy.gsshop import GsShopClient
from backend.domain.samba.tenant.middleware import get_optional_tenant_id
from backend.utils.logger import logger

from ._helpers import _get_setting

router = APIRouter(tags=["samba-proxy"])


# ═══════════════════════════════════════════════
# 11번가 OpenAPI 인증 테스트
# ═══════════════════════════════════════════════


@router.post("/11st/auth-test")
async def elevenst_auth_test(
    session: AsyncSession = Depends(get_read_session_dependency),
    tenant_id: Optional[str] = Depends(get_optional_tenant_id),
) -> dict[str, Any]:
    """11번가 OpenAPI 인증 테스트 — 상품검색 API 호출로 Key 유효성 확인."""
    creds = await _get_setting(session, "store_11st", tenant_id=tenant_id)
    if not creds or not isinstance(creds, dict):
        return {"success": False, "message": "11번가 설정이 저장되지 않았습니다."}

    api_key = creds.get("apiKey", "")
    if not api_key:
        return {"success": False, "message": "Open API Key가 비어있습니다."}

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "http://openapi.11st.co.kr/openapi/OpenApiService.tmall",
                params={
                    "key": api_key,
                    "apiCode": "ProductSearch",
                    "keyword": "test",
                    "pageSize": "1",
                },
            )
            body = resp.text
            # 에러코드 003 = 미등록 API Key
            if "003" in body and "미등록" in body:
                return {"success": False, "message": "등록되지 않은 API Key입니다."}
            if "004" in body and "트래픽" in body:
                return {
                    "success": False,
                    "message": "트래픽 초과입니다. 잠시 후 다시 시도해주세요.",
                }
            if resp.status_code == 200 and "<ProductSearchResponse>" in body:
                return {"success": True, "message": "인증 성공 — API Key가 유효합니다."}
            if resp.status_code == 200:
                # XML 응답이지만 에러일 수 있음
                if "<error>" in body.lower() or "<code>" in body:
                    return {"success": False, "message": "API Key가 유효하지 않습니다."}
                return {"success": True, "message": "인증 성공"}
            return {"success": False, "message": f"HTTP {resp.status_code}"}
    except Exception as exc:
        logger.error(f"[11번가] 인증 테스트 실패: {exc}")
        return {"success": False, "message": f"API 호출 실패: {exc}"}


@router.post("/11st/seller-info")
async def elevenst_seller_info(
    session: AsyncSession = Depends(get_read_session_dependency),
    tenant_id: Optional[str] = Depends(get_optional_tenant_id),
) -> dict[str, Any]:
    """11번가 출고지/반품교환지 주소 조회.

    GET /rest/areaservice/outboundarea (출고지)
    GET /rest/areaservice/inboundarea (반품/교환지)
    """
    from backend.domain.samba.proxy.elevenst import ElevenstClient

    creds = await _get_setting(session, "store_11st", tenant_id=tenant_id)
    if not creds or not isinstance(creds, dict):
        return {"success": False, "message": "11번가 설정이 저장되지 않았습니다."}
    api_key = creds.get("apiKey", "")
    if not api_key:
        return {"success": False, "message": "Open API Key가 비어있습니다."}

    try:
        client = ElevenstClient(api_key)

        # 출고지 + 반품/교환지 동시 조회
        outbound = await client.get_outbound_addresses()
        inbound = await client.get_inbound_addresses()

        if not outbound and not inbound:
            return {
                "success": False,
                "message": "출고지/반품지 정보가 없습니다. 11번가 셀러오피스에서 먼저 등록해주세요.",
            }

        result: dict[str, Any] = {}
        # 첫 번째 출고지 주소 사용
        if outbound:
            first_out = outbound[0]
            result["shipFromAddress"] = first_out.get("addr", "")
            result["shipFromAddrSeq"] = first_out.get("addrSeq", "")
            result["shipFromName"] = first_out.get("addrNm", "")
        # 첫 번째 반품/교환지 주소 사용
        if inbound:
            first_in = inbound[0]
            result["returnAddress"] = first_in.get("addr", "")
            result["returnAddrSeq"] = first_in.get("addrSeq", "")
            result["returnName"] = first_in.get("addrNm", "")

        # 전체 목록도 함께 반환
        result["outboundList"] = outbound
        result["inboundList"] = inbound

        # 발송예정일 템플릿 (베스트 에포트, 실패해도 출고지 응답은 정상 반환)
        try:
            templates = await client.get_dispatch_templates()
            if templates:
                # 대표(reprYn=Y) 우선, 없으면 첫 번째
                rep = next(
                    (t for t in templates if t.get("reprYn") == "Y"), templates[0]
                )
                result["dispatchTemplateNo"] = rep.get("tmpltNo", "")
                result["dispatchTemplateName"] = rep.get("tmpltNm", "")
                result["dispatchTemplateList"] = templates
        except Exception as exc:
            logger.warning(f"[11번가] 발송예정일 템플릿 조회 스킵: {exc}")

        return {"success": True, "message": "출고지/반품지 조회 성공", "data": result}
    except Exception as exc:
        logger.error(f"[11번가] 출고지/반품지 조회 실패: {exc}")
        return {"success": False, "message": f"출고지/반품지 조회 실패: {exc}"}


# ═══════════════════════════════════════════════
# 쿠팡 Wing API 인증 테스트
# ═══════════════════════════════════════════════


@router.post("/coupang/auth-test")
async def coupang_auth_test(
    session: AsyncSession = Depends(get_read_session_dependency),
    tenant_id: Optional[str] = Depends(get_optional_tenant_id),
) -> dict[str, Any]:
    """쿠팡 Wing API 인증 테스트 — HMAC 서명으로 카테고리 조회."""
    creds = await _get_setting(session, "store_coupang", tenant_id=tenant_id)
    if not creds or not isinstance(creds, dict):
        return {"success": False, "message": "쿠팡 설정이 저장되지 않았습니다."}

    access_key = creds.get("accessKey", "")
    secret_key = creds.get("secretKey", "")
    if not access_key or not secret_key:
        return {
            "success": False,
            "message": "Access Key 또는 Secret Key가 비어있습니다.",
        }

    vendor_id = creds.get("vendorId", "")
    if not vendor_id:
        return {"success": False, "message": "Vendor ID가 비어있습니다."}

    try:
        from backend.domain.samba.proxy.coupang import CoupangClient

        client = CoupangClient(access_key, secret_key, vendor_id)
        # 카테고리 조회 API로 인증 테스트 (유효한 엔드포인트)
        await client.get_categories()
        return {"success": True, "message": "인증 성공 — API Key가 유효합니다."}
    except Exception as exc:
        logger.error(f"[쿠팡] 인증 테스트 실패: {exc}")
        return {"success": False, "message": f"인증 실패: {exc}"}


# ═══════════════════════════════════════════════
# 쿠팡 출고지/반품지 조회
# ═══════════════════════════════════════════════


class CoupangShippingPlacesRequest(BaseModel):
    """쿠팡 출고지/반품지 조회 요청 바디."""

    account_id: Optional[str] = None  # ma_xxxx — 없으면 store_coupang 폴백


@router.post("/coupang/shipping-places")
async def coupang_shipping_places(
    body: CoupangShippingPlacesRequest,
    session: AsyncSession = Depends(get_read_session_dependency),
    tenant_id: Optional[str] = Depends(get_optional_tenant_id),
) -> dict[str, Any]:
    """쿠팡 출고지/반품지 전체 목록 조회 (계정별).

    우선순위: SambaMarketAccount.additional_fields → account 컬럼 → store_coupang 전역 폴백
    """
    from backend.domain.samba.account.model import SambaMarketAccount
    from backend.domain.samba.proxy.coupang import CoupangClient

    creds: dict[str, Any] = {}

    # 1) account_id 지정 시 계정별 인증정보 추출
    if body.account_id:
        account = await session.get(SambaMarketAccount, body.account_id)
        if account:
            extras = account.additional_fields or {}
            if isinstance(extras, dict):
                creds = {k: v for k, v in extras.items() if v}
            # account 컬럼 폴백
            if not creds.get("accessKey") and account.api_key:
                creds["accessKey"] = account.api_key
            if not creds.get("secretKey") and account.api_secret:
                creds["secretKey"] = account.api_secret
            if not creds.get("vendorId") and account.seller_id:
                creds["vendorId"] = account.seller_id

    # 2) 폴백: store_coupang (단일계정 환경 호환)
    if not creds.get("accessKey"):
        store = await _get_setting(session, "store_coupang", tenant_id=tenant_id)
        if isinstance(store, dict):
            creds = store

    access_key = creds.get("accessKey", "")
    secret_key = creds.get("secretKey", "")
    vendor_id = creds.get("vendorId", "")

    if not access_key or not secret_key or not vendor_id:
        return {
            "success": False,
            "message": "쿠팡 인증정보(AccessKey/SecretKey/VendorId)가 없습니다.",
            "data": None,
        }

    try:
        client = CoupangClient(access_key, secret_key, vendor_id)
        outbound = await client.get_outbound_shipping_places()
        inbound = await client.get_return_shipping_centers()

        if not outbound and not inbound:
            return {
                "success": False,
                "message": "출고지/반품지 정보가 없습니다. Wing에서 먼저 등록해주세요.",
                "data": {"outboundList": [], "inboundList": []},
            }

        return {
            "success": True,
            "message": "출고지/반품지 조회 성공",
            "data": {
                "outboundList": outbound,
                "inboundList": inbound,
            },
        }
    except Exception as exc:
        logger.error(f"[쿠팡] 출고지/반품지 조회 실패: {exc}")
        return {"success": False, "message": f"조회 실패: {exc}", "data": None}


# ═══════════════════════════════════════════════
# 롯데ON Open API 인증 테스트
# ═══════════════════════════════════════════════


@router.post("/lotteon/auth-test")
async def lotteon_auth_test(
    session: AsyncSession = Depends(get_read_session_dependency),
    tenant_id: Optional[str] = Depends(get_optional_tenant_id),
) -> dict[str, Any]:
    """롯데ON Open API 인증 테스트 — 거래처 정보 조회 + 배송인프라 검증."""
    creds = await _get_setting(session, "store_lotteon", tenant_id=tenant_id)
    if not creds or not isinstance(creds, dict):
        return {"success": False, "message": "롯데ON 설정이 저장되지 않았습니다."}

    api_key = (creds.get("apiKey", "") or "").strip()
    if not api_key:
        return {"success": False, "message": "API Key가 비어있습니다."}

    try:
        from backend.domain.samba.proxy.lotteon import LotteonClient

        client = LotteonClient(api_key)
        result = await client.test_auth()
        data = result.get("data", {})
        tr_info = (
            f" (거래처: {data.get('trGrpCd', '')}-{data.get('trNo', '')})"
            if data
            else ""
        )

        # 배송인프라 입력 여부 확인
        dv_cst_pol = creds.get("dvCstPolNo", "")
        owhp = creds.get("owhpNo", "")
        rtrp = creds.get("rtrpNo", "")
        missing = []
        if not dv_cst_pol:
            missing.append("배송정책번호")
        if not owhp:
            missing.append("출고지번호")
        if not rtrp:
            missing.append("회수지번호")

        infra_msg = ""
        if missing:
            infra_msg = f" ⚠ 미입력: {', '.join(missing)}"

        return {
            "success": True,
            "message": f"인증 성공{tr_info}{infra_msg}",
            "data": {
                **(data or {}),
                "dvCstPolNo": dv_cst_pol,
                "owhpNo": owhp,
                "rtrpNo": rtrp,
            },
        }
    except Exception as exc:
        logger.error(f"[롯데ON] 인증 테스트 실패: {exc}")
        return {"success": False, "message": f"인증 실패: {exc}"}


# ═══════════════════════════════════════════════
# 롯데ON 배송비정책 / 출고지 회수지 목록
# ═══════════════════════════════════════════════


@router.get("/lotteon/delivery-policies")
async def lotteon_delivery_policies(
    session: AsyncSession = Depends(get_read_session_dependency),
    tenant_id: Optional[str] = Depends(get_optional_tenant_id),
) -> dict[str, Any]:
    """롯데ON 배송비정책 목록 조회."""
    creds = await _get_setting(session, "store_lotteon", tenant_id=tenant_id)
    api_key = ((creds or {}).get("apiKey", "") or "").strip()
    if not api_key:
        return {"success": False, "policies": []}
    try:
        from backend.domain.samba.proxy.lotteon import LotteonClient

        client = LotteonClient(api_key)
        await client.test_auth()
        result = await client.get_delivery_policies()
        items = result.get("data", []) or []
        fee_map = {"A": "유료", "B": "무료", "C": "조건부"}
        policies = []
        for item in items:
            if item.get("useYn") != "Y":
                continue
            pol_no = item.get("dvCstPolNo", "")
            pol_nm = item.get("dvCstPolNm", "")
            fee_type = fee_map.get(item.get("dvCstDvsCd", ""), "")
            fee = item.get("dvCst", 0)
            island = item.get("inrmAdtnDvCst", 0)
            parts = [p for p in [pol_no, pol_nm] if p]
            if fee_type:
                parts.append(fee_type)
            try:
                if fee and float(fee):
                    parts.append(f"{int(float(fee)):,}원")
            except (ValueError, TypeError):
                pass
            try:
                if island and float(island):
                    parts.append(f"도서+{int(float(island)):,}원")
            except (ValueError, TypeError):
                pass
            policies.append({"value": pol_no, "label": " / ".join(parts)})
        return {"success": True, "policies": policies}
    except Exception as exc:
        logger.error(f"[롯데ON] 배송비정책 조회 실패: {exc}")
        return {"success": False, "policies": [], "message": str(exc)}


@router.get("/lotteon/warehouses")
async def lotteon_warehouses(
    session: AsyncSession = Depends(get_read_session_dependency),
    tenant_id: Optional[str] = Depends(get_optional_tenant_id),
) -> dict[str, Any]:
    """롯데ON 출고지/회수지 목록 조회."""
    creds = await _get_setting(session, "store_lotteon", tenant_id=tenant_id)
    api_key = ((creds or {}).get("apiKey", "") or "").strip()
    if not api_key:
        return {"success": False, "departure": [], "return_": []}
    try:
        from backend.domain.samba.proxy.lotteon import LotteonClient

        client = LotteonClient(api_key)
        await client.test_auth()
        result = await client.get_warehouses()
        items = result.get("data", []) or []
        departure: list[dict[str, str]] = []
        return_: list[dict[str, str]] = []
        for item in items:
            if item.get("useYn") != "Y":
                continue
            addr = (
                f"{item.get('stnmZipAddr', '')} {item.get('stnmDtlAddr', '')}".strip()
            )
            dvp_no = item.get("dvpNo", "")
            dvp_nm = item.get("dvpNm", "")
            label = f"{dvp_no} / {dvp_nm}" + (f" ({addr})" if addr else "")
            entry = {"value": dvp_no, "label": label}
            if item.get("dvpTypCd") == "02":
                departure.append(entry)
            elif item.get("dvpTypCd") == "01":
                return_.append(entry)
        return {"success": True, "departure": departure, "return_": return_}
    except Exception as exc:
        logger.error(f"[롯데ON] 출고지/회수지 조회 실패: {exc}")
        return {"success": False, "departure": [], "return_": [], "message": str(exc)}


# ═══════════════════════════════════════════════
# SSG Open API 인증 테스트
# ═══════════════════════════════════════════════


@router.post("/ssg/auth-test")
async def ssg_auth_test(
    session: AsyncSession = Depends(get_read_session_dependency),
    tenant_id: Optional[str] = Depends(get_optional_tenant_id),
) -> dict[str, Any]:
    """SSG Open API 인증 테스트 — 브랜드 목록 조회."""
    creds = await _get_setting(session, "store_ssg", tenant_id=tenant_id)
    if not creds or not isinstance(creds, dict):
        return {"success": False, "message": "SSG 설정이 저장되지 않았습니다."}

    api_key = creds.get("apiKey", "")
    if not api_key:
        return {"success": False, "message": "인증키가 비어있습니다."}

    try:
        from backend.domain.samba.proxy.ssg import SSGClient

        client = SSGClient(api_key)
        await client.test_auth()
        return {"success": True, "message": "인증 성공 — API Key가 유효합니다."}
    except Exception as exc:
        logger.error(f"[SSG] 인증 테스트 실패: {exc}")
        return {"success": False, "message": f"인증 실패: {exc}"}


@router.get("/ssg/brands")
async def ssg_brands(
    session: AsyncSession = Depends(get_read_session_dependency),
    tenant_id: Optional[str] = Depends(get_optional_tenant_id),
    account_id: str | None = None,
) -> dict[str, Any]:
    """SSG 계약 브랜드 목록 조회 (계정별).

    account_id 지정 시 해당 계정의 API 키 사용, 없으면 store_ssg 전역 폴백.
    """
    from backend.domain.samba.account.model import SambaMarketAccount

    api_key = ""

    if account_id:
        account = await session.get(SambaMarketAccount, account_id)
        if account:
            extras = account.additional_fields or {}
            if isinstance(extras, dict):
                api_key = extras.get("apiKey", "")
            if not api_key and account.api_key:
                api_key = account.api_key

    if not api_key:
        creds = await _get_setting(session, "store_ssg", tenant_id=tenant_id)
        if isinstance(creds, dict):
            api_key = creds.get("apiKey", "")

    if not api_key:
        return {"success": False, "brands": []}
    try:
        from backend.domain.samba.proxy.ssg import SSGClient

        client = SSGClient(api_key)
        result = await client.get_brands()
        raw = result.get("result", {})
        brand_list = raw.get("brands", [{}])
        if isinstance(brand_list, dict):
            brand_list = [brand_list]
        # XStream 형식: brands[0].brand[] 구조
        actual_brands: list[dict] = []
        for item in brand_list:
            sub = item.get("brand", [])
            if isinstance(sub, dict):
                sub = [sub]
            actual_brands.extend(sub)
        brands = [
            {"brandId": str(b.get("brandId", "")), "brandNm": b.get("brandNm", "")}
            for b in actual_brands
            if b.get("brandId") and b.get("useYn") == "Y"
        ]
        return {"success": True, "brands": brands}
    except Exception as exc:
        logger.error(f"[SSG] 브랜드 조회 실패: {exc}")
        return {"success": False, "brands": [], "message": str(exc)}


@router.get("/ssg/shipping-policies")
async def ssg_shipping_policies(
    session: AsyncSession = Depends(get_read_session_dependency),
    tenant_id: Optional[str] = Depends(get_optional_tenant_id),
    account_id: str | None = None,
) -> dict[str, Any]:
    """SSG 배송비정책 목록 조회."""
    creds = await _get_setting(session, "store_ssg", tenant_id=tenant_id)
    if not creds or not isinstance(creds, dict):
        return {"success": False, "policies": []}
    api_key = creds.get("apiKey", "")
    if not api_key:
        return {"success": False, "policies": []}
    try:
        from backend.domain.samba.proxy.ssg import SSGClient

        client = SSGClient(api_key)
        result = await client.get_shipping_policies()
        raw = result.get("result", {})
        policies_wrapper = raw.get("shppcstPlcys", [{}])
        policy_list = (
            policies_wrapper[0].get("shppcstPlcy", []) if policies_wrapper else []
        )
        policies = []
        for p in policy_list:
            policies.append(
                {
                    "shppcstId": p.get("shppcstId", ""),
                    "feeAmt": p.get("shppcst", 0) or p.get("dlvCstAmt", 0),
                    "prpayCodDivNm": p.get("prpayCodDivNm", ""),
                    "shppcstAplUnitNm": p.get("shppcstAplUnitNm", ""),
                    "divCd": p.get("shppcstPlcyDivCd", 0),
                }
            )
        return {"success": True, "policies": policies}
    except Exception as exc:
        logger.error(f"[SSG] 배송비정책 조회 실패: {exc}")
        return {"success": False, "policies": [], "message": str(exc)}


@router.get("/ssg/addresses")
async def ssg_addresses(
    session: AsyncSession = Depends(get_read_session_dependency),
    tenant_id: Optional[str] = Depends(get_optional_tenant_id),
    account_id: str | None = None,
) -> dict[str, Any]:
    """SSG 출고/반송 주소 목록 조회."""
    creds = await _get_setting(session, "store_ssg", tenant_id=tenant_id)
    if not creds or not isinstance(creds, dict):
        return {"success": False, "addresses": []}
    api_key = creds.get("apiKey", "")
    if not api_key:
        return {"success": False, "addresses": []}
    try:
        from backend.domain.samba.proxy.ssg import SSGClient

        client = SSGClient(api_key)
        result = await client.get_addresses()
        raw = result.get("result", {})
        # SSG 실제 응답: venAddrDelInfo → venAddrDelInfoDto
        addr_list = raw.get("venAddrDelInfo", [])
        if isinstance(addr_list, dict):
            addr_list = [addr_list]
        addrs_raw = addr_list[0].get("venAddrDelInfoDto", []) if addr_list else []
        if isinstance(addrs_raw, dict):
            addrs_raw = [addrs_raw]
        addresses = []
        for a in addrs_raw:
            addresses.append(
                {
                    "grpAddrId": a.get("grpAddrId", ""),
                    "doroAddrId": a.get("doroAddrId", ""),
                    "addrNm": a.get("addrlcAntnmNm", ""),
                    "bascAddr": a.get("doroAddrBasc", ""),
                }
            )
        return {"success": True, "addresses": addresses}
    except Exception as exc:
        logger.error(f"[SSG] 주소 조회 실패: {exc}")
        return {"success": False, "addresses": [], "message": str(exc)}


# ═══════════════════════════════════════════════
# GS샵 인증 테스트 (개발/운영)
# ═══════════════════════════════════════════════


@router.post("/gsshop/auth-test")
async def gsshop_auth_test(
    session: AsyncSession = Depends(get_read_session_dependency),
    tenant_id: Optional[str] = Depends(get_optional_tenant_id),
) -> dict[str, Any]:
    """GS샵 AES256 인증 테스트 — 개발/운영 환경 모두 검증."""
    creds = await _get_setting(session, "store_gsshop", tenant_id=tenant_id)
    if not creds or not isinstance(creds, dict):
        return {"success": False, "message": "GS샵 설정이 저장되지 않았습니다."}

    sup_cd = creds.get("storeId", "")
    api_key_dev = creds.get("apiKeyDev", "")
    api_key_prod = creds.get("apiKeyProd", "")

    if not sup_cd:
        return {"success": False, "message": "스토어 ID(협력사코드)가 비어있습니다."}
    if not api_key_dev and not api_key_prod:
        return {
            "success": False,
            "message": "개발 또는 운영 AES256 인증키를 입력해주세요.",
        }

    results: list[str] = []
    any_ok = False

    # 개발 환경 테스트
    if api_key_dev:
        try:
            dev_client = GsShopClient(sup_cd=sup_cd, aes_key=api_key_dev, env="dev")
            dev_result = await dev_client.check_auth()
            if dev_result.get("authenticated"):
                results.append("개발: 인증 성공")
                any_ok = True
            else:
                results.append(f"개발: {dev_result.get('message', '인증 실패')}")
        except Exception as exc:
            results.append(f"개발: {exc}")

    # 운영 환경 테스트
    if api_key_prod:
        try:
            prod_client = GsShopClient(sup_cd=sup_cd, aes_key=api_key_prod, env="prod")
            prod_result = await prod_client.check_auth()
            if prod_result.get("authenticated"):
                results.append("운영: 인증 성공")
                any_ok = True
            else:
                results.append(f"운영: {prod_result.get('message', '인증 실패')}")
        except Exception as exc:
            results.append(f"운영: {exc}")

    msg = " / ".join(results)
    return {"success": any_ok, "message": msg}


# ═══════════════════════════════════════════════
# 플레이오토 인증 테스트
# ═══════════════════════════════════════════════


@router.post("/playauto/auth-test")
async def playauto_auth_test(
    session: AsyncSession = Depends(get_read_session_dependency),
    tenant_id: Optional[str] = Depends(get_optional_tenant_id),
) -> dict[str, Any]:
    """플레이오토 API 인증 테스트 — 실제 API 호출로 연결 및 인증 확인."""
    from backend.domain.samba.proxy.playauto import PlayAutoClient, PlayAutoApiError

    creds = await _get_setting(session, "store_playauto", tenant_id=tenant_id)
    if not creds or not isinstance(creds, dict):
        return {"success": False, "message": "플레이오토 설정이 저장되지 않았습니다."}

    api_key = creds.get("apiKey", "")
    if not api_key:
        return {"success": False, "message": "API Key가 설정되지 않았습니다."}

    try:
        client = PlayAutoClient(api_key)
        await client.get_market_list()
        return {"success": True, "message": "플레이오토 연결 성공 — API 인증 확인됨"}
    except PlayAutoApiError as e:
        msg = str(e.message)
        if "타임아웃" in msg or "연결 실패" in msg:
            return {
                "success": False,
                "message": "연결 실패 — GCP/클라우드 환경에서 PlayAuto 호스트가 차단됩니다. "
                "설정 > 프록시/IP 설정에서 전송(transmit) 용도 국내 ISP 정적 IP 프록시를 등록하세요.",
            }
        return {"success": False, "message": f"인증 실패: {msg}"}
    except Exception as e:
        return {"success": False, "message": f"인증 테스트 오류: {e}"}


# ═══════════════════════════════════════════════
# 통합 마켓 인증 테스트 (범용)
# ═══════════════════════════════════════════════


@router.post("/market/auth-test/{market_key}")
async def market_auth_test(
    market_key: str,
    session: AsyncSession = Depends(get_read_session_dependency),
    tenant_id: Optional[str] = Depends(get_optional_tenant_id),
) -> dict[str, Any]:
    """범용 마켓 인증 테스트 — 설정값 존재 여부 확인."""
    creds = await _get_setting(session, f"store_{market_key}", tenant_id=tenant_id)
    if not creds or not isinstance(creds, dict):
        return {
            "success": False,
            "message": f"{market_key} 설정이 저장되지 않았습니다.",
        }

    # 빈 값 체크
    has_value = any(v for v in creds.values() if v and str(v).strip())
    if not has_value:
        return {"success": False, "message": "설정값이 비어있습니다."}

    return {"success": True, "message": "설정 저장됨 — 상품 전송 시 연동됩니다."}
