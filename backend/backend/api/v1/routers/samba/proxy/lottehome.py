"""롯데홈쇼핑 관련 엔드포인트."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel.ext.asyncio.session import AsyncSession

from backend.db.orm import get_read_session_dependency, get_write_session_dependency
from backend.domain.samba.proxy.lottehome import LotteApiError, LotteHomeClient
from backend.domain.samba.tenant.middleware import get_optional_tenant_id

from backend.utils.logger import logger

from ._helpers import _get_lotte_client, _set_setting

router = APIRouter(tags=["samba-proxy"])


class LotteAuthRequest(BaseModel):
    userId: str
    password: str
    agncNo: Optional[str] = ""
    env: Optional[str] = "test"


@router.get("/lottehome/policy")
async def get_lottehome_policy(
    session: AsyncSession = Depends(get_read_session_dependency),
    tenant_id: Optional[str] = Depends(get_optional_tenant_id),
) -> dict[str, Any]:
    """롯데홈쇼핑 정책 조회."""
    from ._helpers import _get_setting

    policy = await _get_setting(session, "lottehome_policy", tenant_id=tenant_id) or {}
    return {"success": True, "data": policy}


@router.post("/lottehome/policy")
async def save_lottehome_policy(
    body: dict,
    write_session: AsyncSession = Depends(get_write_session_dependency),
    tenant_id: Optional[str] = Depends(get_optional_tenant_id),
) -> dict[str, Any]:
    """롯데홈쇼핑 정책 저장."""
    from ._helpers import _set_setting

    await _set_setting(write_session, "lottehome_policy", body, tenant_id=tenant_id)
    return {"success": True}


@router.post("/lottehome/auth")
async def lottehome_auth(
    body: LotteAuthRequest,
    write_session: AsyncSession = Depends(get_write_session_dependency),
    tenant_id: Optional[str] = Depends(get_optional_tenant_id),
) -> dict[str, Any]:
    """롯데홈쇼핑 인증키 발급."""
    if not body.userId or not body.password:
        raise HTTPException(
            status_code=400, detail="협력업체ID와 비밀번호를 입력해주세요."
        )
    # 기존 credentials와 정책 로드 (정책의 배송지/MD상품군/카테고리 병합)
    from ._helpers import _get_setting

    existing_creds = (
        await _get_setting(write_session, "lottehome_credentials", tenant_id=tenant_id)
        or {}
    )
    policy = (
        await _get_setting(write_session, "lottehome_policy", tenant_id=tenant_id) or {}
    )

    creds_to_save = dict(existing_creds)
    creds_to_save.update(
        {
            "userId": body.userId,
            "password": body.password,
            "agncNo": body.agncNo or "",
            "env": body.env or "test",
        }
    )

    # 정책의 배송지/MD상품군/카테고리 정보 병합
    if policy:
        creds_to_save.update(
            {
                "disp_no": policy.get("disp_no", creds_to_save.get("disp_no", "")),
                "md_gsgr_no": policy.get(
                    "md_gsgr_no", creds_to_save.get("md_gsgr_no", "")
                ),
                "dlv_polc_no": policy.get(
                    "dlv_polc_no", creds_to_save.get("dlv_polc_no", "")
                ),
                "corp_dlvp_sn": policy.get(
                    "corp_dlvp_sn", creds_to_save.get("corp_dlvp_sn", "")
                ),
                "corp_rls_pl_sn": policy.get(
                    "corp_rls_pl_sn", creds_to_save.get("corp_rls_pl_sn", "")
                ),
            }
        )

    # DB에 자격증명 저장 (정책 정보 포함)
    await _set_setting(
        write_session,
        "lottehome_credentials",
        creds_to_save,
        tenant_id=tenant_id,
    )
    client = LotteHomeClient(
        user_id=body.userId,
        password=body.password,
        agnc_no=body.agncNo or "",
        env=body.env or "test",
    )
    try:
        return await client.authenticate()
    except LotteApiError as exc:
        logger.warning(f"[롯데홈] 인증 실패 (LotteApiError): {exc}")
        return {"success": False, "message": str(exc), "code": exc.code}
    except Exception as exc:
        logger.error(f"[롯데홈] 인증 예외: {exc}")
        return {"success": False, "message": str(exc), "code": "AUTH_FAILED"}


@router.get("/lottehome/auth/status")
async def lottehome_auth_status(
    session: AsyncSession = Depends(get_read_session_dependency),
) -> dict[str, Any]:
    """롯데홈쇼핑 캐시된 인증 상태."""
    # 상태 없음 반환 (서버 인스턴스별 인증 캐시는 LotteHomeClient 인스턴스에 있으므로)
    return {"authenticated": False, "message": "인증 정보 없음 (재인증 필요)"}


@router.get("/lottehome/brands")
async def lottehome_brands(
    brnd_nm: str = Query(""),
    session: AsyncSession = Depends(get_read_session_dependency),
) -> dict[str, Any]:
    """롯데홈쇼핑 브랜드 목록 조회."""
    client = await _get_lotte_client(session)
    logger.info(
        f"[롯데홈] 브랜드 검색 요청: brnd_nm={brnd_nm!r}, env={client.env}, user_id={client.user_id!r}"
    )
    try:
        result = await client.search_brands(brnd_nm)
        data = result.get("data", {})
        result_block = data.get("Result", data) if isinstance(data, dict) else {}
        brand_list_raw = (
            result_block.get("BrandInfoList")
            if isinstance(result_block, dict)
            else None
        )
        logger.info(
            f"[롯데홈] 브랜드 응답 구조: BrandInfoList type={type(brand_list_raw).__name__}, keys={list(result_block.keys()) if isinstance(result_block, dict) else 'N/A'}"
        )
        # BrandInfoList가 flat list(각 항목이 브랜드) or dict(BrandInfo 자식) 둘 다 처리
        if isinstance(brand_list_raw, list):
            brands = brand_list_raw
        elif isinstance(brand_list_raw, dict):
            brand_info = brand_list_raw.get("BrandInfo", [])
            brands = (
                brand_info
                if isinstance(brand_info, list)
                else ([brand_info] if brand_info else [])
            )
        else:
            brands = []
        logger.info(f"[롯데홈] 브랜드 검색 결과: {len(brands)}건")
        normalized = dict(result_block) if isinstance(result_block, dict) else {}
        normalized["BrandInfoList"] = {"BrandInfo": brands}
        return {"success": True, "data": {"Result": normalized}}
    except LotteApiError as exc:
        logger.warning(f"[롯데홈] 브랜드 조회 실패: code={exc.code}, message={exc}")
        return {"success": False, "message": str(exc), "code": exc.code}
    except Exception as exc:
        logger.exception(f"[롯데홈] 브랜드 조회 예외: {exc}")
        return {"success": False, "message": str(exc)}


@router.get("/lottehome/categories")
async def lottehome_categories(
    disp_tp_cd: str = Query(""),
    md_gsgr_no: str = Query(""),
    session: AsyncSession = Depends(get_read_session_dependency),
) -> dict[str, Any]:
    """롯데홈쇼핑 전시카테고리 목록 조회."""
    client = await _get_lotte_client(session)
    try:
        result = await client.search_categories(disp_tp_cd, md_gsgr_no)
        return {"success": True, "data": result.get("data")}
    except LotteApiError as exc:
        logger.warning(f"[롯데홈] 카테고리 조회 실패: {exc}")
        return {"success": False, "message": str(exc), "code": exc.code}


@router.get("/lottehome/md-groups")
async def lottehome_md_groups(
    session: AsyncSession = Depends(get_read_session_dependency),
) -> dict[str, Any]:
    """롯데홈쇼핑 MD상품군 조회."""
    client = await _get_lotte_client(session)
    try:
        result = await client.search_md_groups()
        if not result.get("success"):
            msg = result.get("message", "MD상품군 조회 실패")
            logger.warning(f"[롯데홈] MD상품군 조회 실패: {msg}")
            return {"success": False, "message": msg}
        return {"success": True, "data": result.get("data")}
    except LotteApiError as exc:
        logger.warning(f"[롯데홈] MD상품군 조회 실패: {exc}")
        return {"success": False, "message": str(exc), "code": exc.code}


@router.get("/lottehome/standard-categories")
async def lottehome_standard_categories(
    disp_no: str = Query(""),
    session: AsyncSession = Depends(get_read_session_dependency),
) -> dict[str, Any]:
    """전시카테고리에 매핑된 표준카테고리 목록 조회."""
    if not disp_no:
        return {"success": False, "message": "disp_no가 필요합니다.", "code": "0005"}
    client = await _get_lotte_client(session)
    try:
        result = await client.search_standard_categories(disp_no)
        return {"success": True, "data": result.get("data")}
    except LotteApiError as exc:
        logger.warning(f"[롯데홈] 표준카테고리 조회 실패: {exc}")
        return {"success": False, "message": str(exc), "code": exc.code}


@router.get("/lottehome/delivery-policies")
async def lottehome_delivery_policies(
    session: AsyncSession = Depends(get_read_session_dependency),
) -> dict[str, Any]:
    """롯데홈쇼핑 배송비정책 조회 — policies 리스트로 파싱해서 반환."""
    client = await _get_lotte_client(session)
    try:
        result = await client.search_delivery_policies()
        data = result.get("data", {})
        logger.info(
            f"[롯데홈] 배송비정책 raw data keys: {list(data.keys()) if isinstance(data, dict) else type(data)}"
        )
        result_root = data.get("Result", data) if isinstance(data, dict) else {}
        pol_list = result_root.get(
            "DlvPolcInfoList",
            result_root.get("DlvPolcList", result_root.get("DlvPolcInfo", {})),
        )
        if isinstance(pol_list, dict):
            items = pol_list.get("DlvPolcInfo", [])
        else:
            items = pol_list
        if isinstance(items, dict):
            items = [items]
        logger.info(
            f"[롯데홈] 배송비정책 items count={len(items) if isinstance(items, list) else 'N/A'}, sample={str(items[:1])[:400] if isinstance(items, list) else items}"
        )

        def _policy_label(item: dict) -> str:
            no = str(item.get("DlvPolcNo", item.get("dlv_polc_no", "")))
            nm = (
                item.get("DlvPolcNm")
                or item.get("dlv_polc_nm")
                or item.get("LwstEntrNm")
                or ""
            )
            dlex = item.get("Dlex", "")
            rtgs = item.get("RtgsDlex", "")
            parts = [nm] if nm else []
            if dlex:
                parts.append(f"기본 {dlex}원")
            if rtgs:
                parts.append(f"반품 {rtgs}원")
            return f"[{no}] {' / '.join(parts)}" if parts else no

        policies = [
            {
                "no": str(item.get("DlvPolcNo", item.get("dlv_polc_no", ""))),
                "nm": _policy_label(item),
            }
            for item in (items if isinstance(items, list) else [])
            if item.get("DlvPolcNo") or item.get("dlv_polc_no")
        ]
        # 추가배송비정책 (도서산간) — IsmrDlvPolcInfoList
        ismr_wrap = result_root.get("IsmrDlvPolcInfoList", {})
        if isinstance(ismr_wrap, dict):
            ismr_items = ismr_wrap.get(
                "IsmrDlvPolcInfo", ismr_wrap.get("DlvPolcInfo", [])
            )
        else:
            ismr_items = ismr_wrap
        if isinstance(ismr_items, dict):
            ismr_items = [ismr_items]
        logger.info(
            f"[롯데홈] 추가배송비정책 ismr_items count={len(ismr_items) if isinstance(ismr_items, list) else 'N/A'}, sample={str(ismr_items[:1])[:400] if isinstance(ismr_items, list) else ismr_items}"
        )

        def _extra_policy_label(item: dict) -> str:
            no = str(item.get("IsmrDlvPolcNo", item.get("DlvPolcNo", "")))
            ismr = item.get("IsmrDlex", "")
            jeju = item.get("JejuDlex", "")
            parts = []
            if ismr:
                parts.append(f"도서산간 {ismr}원")
            if jeju:
                parts.append(f"제주 {jeju}원")
            return f"[{no}] {' / '.join(parts)}" if parts else no

        extra_policies = [
            {
                "no": str(
                    item.get(
                        "IsmrDlvPolcNo",
                        item.get("DlvPolcNo", item.get("dlv_polc_no", "")),
                    )
                ),
                "nm": _extra_policy_label(item),
            }
            for item in (ismr_items if isinstance(ismr_items, list) else [])
            if item.get("IsmrDlvPolcNo")
            or item.get("DlvPolcNo")
            or item.get("dlv_polc_no")
        ]
        return {"success": True, "policies": policies, "extra_policies": extra_policies}
    except LotteApiError as exc:
        logger.warning(f"[롯데홈] 배송비정책 조회 실패: {exc}")
        return {"success": False, "message": str(exc), "code": exc.code}


@router.get("/lottehome/delivery-places")
async def lottehome_delivery_places(
    session: AsyncSession = Depends(get_read_session_dependency),
) -> dict[str, Any]:
    """롯데홈쇼핑 배송지 조회 — shipping_places(출고지) / return_places(반품지) 구조로 반환."""
    client = await _get_lotte_client(session)
    try:
        return await client.search_return_places()
    except LotteApiError as exc:
        logger.warning(f"[롯데홈] 배송지 조회 실패: {exc}")
        return {"success": False, "message": str(exc), "code": exc.code}


@router.post("/lottehome/goods")
async def lottehome_register_goods(
    goods_data: dict[str, Any],
    session: AsyncSession = Depends(get_read_session_dependency),
) -> dict[str, Any]:
    """롯데홈쇼핑 신규상품등록."""
    client = await _get_lotte_client(session)
    try:
        result = await client.register_goods(goods_data)
        return {"success": True, "data": result.get("data")}
    except LotteApiError as exc:
        logger.error(f"[롯데홈] 신규상품등록 실패: {exc}")
        return {"success": False, "message": str(exc), "code": exc.code}


@router.put("/lottehome/goods/new/{goods_req_no}")
async def lottehome_update_new_goods(
    goods_req_no: str,
    goods_data: dict[str, Any],
    session: AsyncSession = Depends(get_read_session_dependency),
) -> dict[str, Any]:
    """롯데홈쇼핑 신규상품수정."""
    client = await _get_lotte_client(session)
    try:
        result = await client.update_new_goods(goods_req_no, goods_data)
        return {"success": True, "data": result.get("data")}
    except LotteApiError as exc:
        logger.error(f"[롯데홈] 신규상품수정 실패 (req={goods_req_no}): {exc}")
        return {"success": False, "message": str(exc), "code": exc.code}


@router.put("/lottehome/goods/display/{goods_no}")
async def lottehome_update_display_goods(
    goods_no: str,
    goods_data: dict[str, Any],
    session: AsyncSession = Depends(get_read_session_dependency),
) -> dict[str, Any]:
    """롯데홈쇼핑 전시상품수정."""
    client = await _get_lotte_client(session)
    try:
        result = await client.update_display_goods(goods_no, goods_data)
        return {"success": True, "data": result.get("data")}
    except LotteApiError as exc:
        logger.error(f"[롯데홈] 전시상품수정 실패 (goods_no={goods_no}): {exc}")
        return {"success": False, "message": str(exc), "code": exc.code}


class LotteSaleStatusRequest(BaseModel):
    sale_stat_cd: str = "20"


@router.patch("/lottehome/goods/{goods_no}/status")
async def lottehome_sale_status(
    goods_no: str,
    body: LotteSaleStatusRequest,
    session: AsyncSession = Depends(get_read_session_dependency),
) -> dict[str, Any]:
    """롯데홈쇼핑 판매상태 변경."""
    client = await _get_lotte_client(session)
    try:
        result = await client.update_sale_status(goods_no, body.sale_stat_cd)
        return {"success": True, "data": result.get("data")}
    except LotteApiError as exc:
        logger.warning(f"[롯데홈] 판매상태 변경 실패 (goods_no={goods_no}): {exc}")
        return {"success": False, "message": str(exc), "code": exc.code}


class LotteStockUpdateRequest(BaseModel):
    goods_no: str
    item_no: str
    inv_qty: int


@router.put("/lottehome/stock")
async def lottehome_update_stock(
    body: LotteStockUpdateRequest,
    session: AsyncSession = Depends(get_read_session_dependency),
) -> dict[str, Any]:
    """롯데홈쇼핑 재고수정."""
    client = await _get_lotte_client(session)
    try:
        result = await client.update_stock(body.goods_no, body.item_no, body.inv_qty)
        return {"success": True, "data": result.get("data")}
    except LotteApiError as exc:
        logger.error(f"[롯데홈] 재고수정 실패 (goods_no={body.goods_no}): {exc}")
        return {"success": False, "message": str(exc), "code": exc.code}


@router.get("/lottehome/stock")
async def lottehome_search_stock(
    goods_no: str = Query(""),
    session: AsyncSession = Depends(get_read_session_dependency),
) -> dict[str, Any]:
    """롯데홈쇼핑 재고 조회."""
    client = await _get_lotte_client(session)
    try:
        result = await client.search_stock(goods_no)
        return {"success": True, "data": result.get("data")}
    except LotteApiError as exc:
        logger.warning(f"[롯데홈] 재고 조회 실패 (goods_no={goods_no}): {exc}")
        return {"success": False, "message": str(exc), "code": exc.code}


@router.post("/lottehome/qa/sync")
async def lottehome_qa_sync(
    session: AsyncSession = Depends(get_write_session_dependency),
) -> dict[str, Any]:
    """롯데홈쇼핑 승인 대기 상품 상태 동기화.

    market_product_nos에 {account_id}_qa=pending 인 상품을 조회하여
    승인 완료된 상품은 approved로 업데이트.
    """
    from sqlmodel import select
    from backend.domain.samba.collector.model import SambaCollectedProduct

    client = await _get_lotte_client(session)

    # pending 상품 조회
    stmt = select(SambaCollectedProduct).where(
        SambaCollectedProduct.market_product_nos != None
    )
    result = await session.execute(stmt)
    products = result.scalars().all()

    updated = 0
    checked = 0

    for product in products:
        m_nos = product.market_product_nos or {}
        pending_accounts = [
            k.replace("_qa", "")
            for k, v in m_nos.items()
            if k.endswith("_qa") and v == "pending"
        ]
        if not pending_accounts:
            continue

        for acc_id in pending_accounts:
            goods_no = m_nos.get(acc_id, "")
            if not goods_no:
                continue
            checked += 1
            try:
                detail = await client.search_goods_view(goods_no)
                data = detail.get("data", {})
                result = data.get("Result", data)
                goods_info = (
                    result.get("GoodsInfo", result)
                    if isinstance(result, dict)
                    else result
                )
                sale_stat = str(goods_info.get("SaleStatCd", "") or "")
                qa_result = str(goods_info.get("QaRsltCd", "") or "")
                # 판매진행(10) 또는 QA 합격(10/15/30) → 승인 완료
                if sale_stat == "10" or qa_result in ("10", "15", "30"):
                    new_nos = dict(m_nos)
                    new_nos[f"{acc_id}_qa"] = "approved"
                    from sqlalchemy import update as sa_update
                    from backend.domain.samba.collector.model import (
                        SambaCollectedProduct as _CP,
                    )

                    await session.execute(
                        sa_update(_CP)
                        .where(_CP.id == product.id)
                        .values(market_product_nos=new_nos)
                    )
                    await session.commit()
                    updated += 1
                    logger.info(
                        f"[롯데홈쇼핑 QA] {product.id} → approved (goods_no={goods_no})"
                    )
            except Exception as e:
                logger.warning(f"[롯데홈쇼핑 QA] {goods_no} 체크 실패: {e}")

    return {"success": True, "checked": checked, "updated": updated}
