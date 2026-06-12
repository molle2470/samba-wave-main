"""#408 회귀테스트 — 쿠팡 다중옵션 비대표 옵션 주문 sellerProductId 폴백 매칭.

배경:
- 쿠팡 다중옵션 리스팅은 옵션마다 productId/vendorItemId 가 다름.
- samba 는 상품당 '대표 옵션 1개'의 productId(`{acc}_pid`)·vendorItemId(`{acc}_vid`)만
  인덱싱 → 비대표 옵션 주문은 둘 다 인덱스에 없어 미등록(cost=0, 허구 수익률).
- 해결: bare `{acc}` 키 = sellerProductId(상품당 1개·옵션무관 안정키)로 폴백 매칭.
  실측: cp_01KPW31RVK9XX5R0FVPWRCDDJZ(나이키 유니콘 양말, songilcp),
  sellerProductId=16201950919, 비대표 옵션 026 → productId 9352667735 미인덱스.

테스트 방식:
- order.py 는 모듈 최상단에서 dtos.user ↔ domain.user 순환참조를 유발해 cold import
  불가(test_coupang_cancel_map_fallback.py 와 동일 제약). → 소스 정적 계약 검증으로
  fix 라인이 제거/변형되는 회귀 PR 머지를 차단한다.
"""

from pathlib import Path
import re

BACKEND_ROOT = Path(__file__).resolve().parents[1]
ORDER_PY = BACKEND_ROOT / "backend/api/v1/routers/samba/order.py"


def _src() -> str:
    return ORDER_PY.read_text(encoding="utf-8")


class TestSellerProductIdPreserved:
    """주문 인입 dict 가 sellerProductId 를 product_id 와 별도로 보존."""

    def test_seller_product_id_key_present(self) -> None:
        src = _src()
        assert '"seller_product_id": str(first_item.get("sellerProductId"' in src, (
            "쿠팡 주문 dict 에 seller_product_id 보존 누락 — product_id 에 합쳐 버리면 "
            "비대표 옵션 폴백(#408)이 동작 못 함"
        )


class TestMatcherSellerProductIdFallback:
    """매처에 sellerProductId 글로벌 폴백(3.6) 존재 + 안전 가드."""

    def test_fallback_block_present(self) -> None:
        src = _src()
        assert 'order_data.get("seller_product_id")' in src, (
            "매처 sellerProductId 폴백 누락 (#408)"
        )
        assert "_mpn_global.get(_spid)" in src, "sellerProductId 글로벌 조회 누락"

    def test_fallback_has_ambiguous_guard(self) -> None:
        """폴백 블록이 ambiguous 충돌을 거부해야 오매칭(엉뚱한 cp) 방지."""
        src = _src()
        idx = src.find('_spid = str(order_data.get("seller_product_id")')
        assert idx != -1, "sellerProductId 폴백 블록 미발견"
        block = src[idx : idx + 320]
        assert "_mpn_global.get(_spid)" in block, "글로벌 조회 누락"
        assert 'not _cand.get("ambiguous")' in block, (
            "ambiguous 가드 누락 — 다중 cp 충돌 시 오매칭 위험"
        )

    def test_fallback_runs_after_product_id_attempt(self) -> None:
        """product_id 글로벌 매칭(3) 시도 뒤에 발동 → 기존 매칭 우선, 회귀 없음."""
        src = _src()
        pid_attempt = src.find("_cand = _mpn_global.get(_pid)")
        spid_attempt = src.find("_cand = _mpn_global.get(_spid)")
        assert pid_attempt != -1 and spid_attempt != -1
        assert pid_attempt < spid_attempt, (
            "sellerProductId 폴백이 product_id attempt 보다 먼저 옴 — 우선순위 회귀"
        )

    def test_vendor_item_id_fallback_retained(self) -> None:
        """#398 vendor_item_id 폴백(3.7)도 함께 유지(통합)."""
        src = _src()
        assert "_mpn_global.get(_vid)" in src, "#398 vendor_item_id 폴백 회귀"

    def test_fallback_chain_order_spid_before_vid(self) -> None:
        """폴백 순서 = product_id → sellerProductId → vendor_item_id."""
        src = _src()
        order = [
            src.find("_cand = _mpn_global.get(_pid)"),
            src.find("_cand = _mpn_global.get(_spid)"),
            src.find("_cand = _mpn_global.get(_vid)"),
        ]
        assert all(i != -1 for i in order), "폴백 단계 일부 누락"
        assert order == sorted(order), "폴백 단계 순서 회귀 (pid → spid → vid 기대)"


class TestIssueReferenced:
    """수정 의도(#408) 주석으로 명시 — 추후 제거 PR 의 맥락 보존."""

    def test_issue_number_in_comment(self) -> None:
        src = _src()
        # sellerProductId 폴백 주변에 #408 언급
        idx = src.find('_spid = str(order_data.get("seller_product_id")')
        head = src[max(0, idx - 400) : idx]
        assert re.search(r"#408", head), "sellerProductId 폴백에 #408 출처 주석 누락"
