"""발주전자료 내부 API.

Claude 클라우드 스케줄잡이 호출하는 전용 엔드포인트.
마스터 엑셀 '샵마인' 시트 발주전자료 구간의 주문번호로 프로덕션 samba_order를
조회해 P(소싱주문번호)/Q(매입금액)/U(택배비)/상태를 반환한다.

samba_auth(JWT)를 우회하므로 X-Internal-Token 헤더로만 인증한다(CS 내부 API와
동일 토큰 cs_internal_token 재사용). app_factory에서 samba_auth 없이 등록된다.

엔드포인트:
  POST /internal/balju/lookup  주문번호 묶음 → 행별 매칭 결과(P/Q/U/status)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import bindparam
from sqlalchemy import text as sa_text
from sqlmodel.ext.asyncio.session import AsyncSession

from backend.api.v1.routers.samba.cs_internal import _require_internal_token
from backend.db.orm import get_read_session_dependency

router = APIRouter(prefix="/internal/balju", tags=["samba-balju-internal"])


# ==================== 요청/응답 모델 ====================


class BaljuRowReq(BaseModel):
    """엑셀 한 행의 매칭 입력."""

    row: int  # 엑셀 행번호 (식별용, 백엔드는 그대로 반환)
    order_keys: List[
        str
    ] = []  # H(오픈마켓 주문번호) — GS는 공백 통째+분리 토큰 모두 가능
    name_hint: Optional[str] = None  # 역검색용 스타일코드/상품명 일부 (쿠팡/GS)
    option_hint: Optional[str] = None  # 다중매칭 디스앰비용 옵션(예: "BK,32", "230")


class BaljuLookupReq(BaseModel):
    rows: List[BaljuRowReq]


_SELECT_COLS = (
    "order_number, od_no, ext_order_number, sourcing_order_number, "
    "cost, shipping_fee, source, status, product_name, product_option, paid_at"
)


def _normalize_opt(s: Optional[str]) -> str:
    """옵션 비교용 정규화 — 공백/구두점 제거, 소문자."""
    if not s:
        return ""
    return "".join(ch for ch in str(s).lower() if ch.isalnum())


def _pick(cands: List[Dict[str, Any]], option_hint: Optional[str]) -> Dict[str, Any]:
    """다중 후보 중 best 선택.

    우선순위:
      1) option_hint 토큰이 product_option 에 포함되는 후보
      2) sourcing_order_number/cost 가 실제로 채워진 후보(취소·빈 행보다 우선)
      3) 최신 paid_at
    """
    hint = _normalize_opt(option_hint)

    def has_value(c: Dict[str, Any]) -> bool:
        return bool(c.get("sourcing_order_number")) or bool(c.get("cost"))

    def opt_match(c: Dict[str, Any]) -> bool:
        if not hint:
            return False
        opt = _normalize_opt(c.get("product_option"))
        # 짧은 hint(예: "85")는 부분일치가 오탐 잦으므로 양방향 포함만 인정
        return hint in opt or opt in hint if opt else False

    def paid_key(c: Dict[str, Any]):
        return c.get("paid_at") or ""

    return sorted(
        cands,
        key=lambda c: (opt_match(c), has_value(c), paid_key(c)),
        reverse=True,
    )[0]


def _decide(
    status: Optional[str],
    sourcing_no: Optional[str],
    cost: float,
    shipping_fee: float,
) -> Dict[str, Any]:
    """status·실값으로 엑셀 기입 지시 산출.

    반환 p_write/q_write/u_write 중 None = 해당 셀 미터치(기존값 보존).
    수동 작업서 확정한 규칙을 Python에 고정 — 클라이언트(스케줄러)는 기계적 적용만.

    - cancelled → P="취소완료" (Q/U 보존: 취소 시 _finalize_cancelled가 cost=0 박으므로
      엑셀 기존 예상매입가를 0으로 덮으면 안 됨)
    - cancelling → P="취소중" (Q/U 보존)
    - 실값 존재(소싱주문번호 또는 cost>0) → P/Q/U 기입 (U는 0 포함)
    - 그 외(delivered·pending 등 + 값없음) → 전부 미터치
    """
    s = (status or "").strip().lower()
    if s == "cancelled":
        return {"p_write": "취소완료", "q_write": None, "u_write": None}
    if s == "cancelling":
        return {"p_write": "취소중", "q_write": None, "u_write": None}
    has_val = bool(sourcing_no) or (cost and cost > 0)
    if has_val:
        return {
            "p_write": str(sourcing_no) if sourcing_no else None,
            "q_write": cost if (cost and cost > 0) else None,
            "u_write": shipping_fee,
        }
    return {"p_write": None, "q_write": None, "u_write": None}


# ==================== 엔드포인트 ====================


@router.post("/lookup", dependencies=[Depends(_require_internal_token)])
async def lookup(
    body: BaljuLookupReq,
    session: AsyncSession = Depends(get_read_session_dependency),
) -> Dict[str, Any]:
    """행별 주문번호 → samba_order 매칭. P/Q/U/status 반환.

    1차: order_number / od_no / ext_order_number IN order_keys
    2차(1차 실패 + name_hint): product_name ILIKE '%name_hint%' 역검색
    다중매칭은 option_hint 로 디스앰비.
    """
    rows = body.rows or []
    all_keys = sorted({k for r in rows for k in (r.order_keys or []) if k})

    # 1차 일괄 매칭 — 키별 인덱싱
    matched_by_key: Dict[str, List[Dict[str, Any]]] = {}
    if all_keys:
        stmt = sa_text(
            f"SELECT {_SELECT_COLS} FROM samba_order "
            "WHERE order_number IN :keys OR od_no IN :keys "
            "OR ext_order_number IN :keys"
        ).bindparams(bindparam("keys", expanding=True))
        res = await session.execute(stmt, {"keys": all_keys})
        for m in res.mappings():
            d = dict(m)
            for k in (d["order_number"], d["od_no"], d["ext_order_number"]):
                if k:
                    matched_by_key.setdefault(str(k), []).append(d)

    results: List[Dict[str, Any]] = []
    for r in rows:
        cands: List[Dict[str, Any]] = []
        seen = set()
        for k in r.order_keys or []:
            for m in matched_by_key.get(k, []):
                if id(m) in seen:
                    continue
                seen.add(id(m))
                cands.append(m)
        via = "order_key" if cands else None

        # 2차: 역검색 (쿠팡 묶음번호·GS 2토큰 등 1차 실패 시)
        if not cands and r.name_hint:
            res2 = await session.execute(
                sa_text(
                    f"SELECT {_SELECT_COLS} FROM samba_order "
                    "WHERE product_name ILIKE :pat "
                    "ORDER BY paid_at DESC NULLS LAST LIMIT 20"
                ),
                {"pat": f"%{r.name_hint}%"},
            )
            cands = [dict(x) for x in res2.mappings()]
            via = "name_hint" if cands else None

        if not cands:
            # 미매칭 — 전부 미터치(기존 엑셀값 보존)
            results.append(
                {
                    "row": r.row,
                    "matched": False,
                    "p_write": None,
                    "q_write": None,
                    "u_write": None,
                }
            )
            continue

        best = _pick(cands, r.option_hint)
        cost = float(best.get("cost") or 0)
        shipping_fee = float(best.get("shipping_fee") or 0)
        decision = _decide(
            best.get("status"),
            best.get("sourcing_order_number"),
            cost,
            shipping_fee,
        )
        results.append(
            {
                "row": r.row,
                "matched": True,
                "match_via": via,
                "source": best.get("source"),
                "status": best.get("status"),
                "sourcing_order_number": best.get("sourcing_order_number"),
                "cost": cost,
                "shipping_fee": shipping_fee,
                "product_name": best.get("product_name"),
                "product_option": best.get("product_option"),
                # 엑셀 기입 지시 (None = 미터치)
                "p_write": decision["p_write"],
                "q_write": decision["q_write"],
                "u_write": decision["u_write"],
            }
        )

    matched_n = sum(1 for x in results if x.get("matched"))
    return {"results": results, "total": len(results), "matched": matched_n}
