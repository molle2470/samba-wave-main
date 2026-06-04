"""롯데ON 자동취소 false-success 회귀 테스트.

배경 (2026-06-04 사용자 보고):
  - 삼바에서 "취소승인" 누르면 cancelled 로 표시되지만 롯데ON 어드민에는
    '발송전 취소요청 목록'에 그대로 남아있는 사고.
  - 진앞 1: ord_row.order_number 가 'L02682376475_2682376509' 형식인데
    클레임 odNo 는 순수 숫자 → 매칭 실패 → 직접취소 fallback.
  - 진앞 2: 직접취소 3006 응답을 seller_cancel_order 가 success 로 처리.
  - 두 진앞이 합쳐져 '미승인 클레임 그대로인데 삼바만 cancelled' 가 발생.

본 테스트:
  1) odNo 후보 추출 헬퍼: '_' 분리 + 'L' prefix 제거 후보 포함을 검증.
  2) seller_cancel_order 3006 응답이 fail 로 신호되는지 검증
     (이전엔 True 반환했고, 그게 false-success 의 직접 원인이었음).
"""

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.api.v1.routers.samba.proxy.sourcing import _lotteon_candidate_od_nos
from backend.domain.samba.proxy.lotteon.api_client import LotteonClient
from backend.domain.samba.proxy.lotteon.api_client import LotteonApiError


class TestCandidateOdNos:
    def test_plain_ord_no_returns_self(self) -> None:
        assert _lotteon_candidate_od_nos("20260601139900099") == ["20260601139900099"]

    def test_underscore_suffix_split(self) -> None:
        # 상품주문번호 형식: 본번_옵션순번
        out = _lotteon_candidate_od_nos("20260601139900099_1")
        assert "20260601139900099_1" in out
        assert "20260601139900099" in out

    def test_l_prefix_stripped(self) -> None:
        # 사용자 사례: 'L02682376475_2682376509'
        out = _lotteon_candidate_od_nos("L02682376475_2682376509")
        assert "L02682376475_2682376509" in out  # 원본
        assert "L02682376475" in out             # _ 앞부분
        assert "02682376475" in out              # L 제거된 순수 숫자

    def test_blank_returns_empty(self) -> None:
        assert _lotteon_candidate_od_nos("") == []
        assert _lotteon_candidate_od_nos(None) == []  # type: ignore[arg-type]


class TestSellerCancel3006NoFalseSuccess:
    """seller_cancel_order 의 3006 응답이 fail 로 처리되어야 한다.

    이전 코드는 'return True, "이미 취소된 주문"' 으로 success 처리했고, 이게
    false-success 의 직접 원인이었다. 본 테스트는 그 회귀가 다시 들어오지 않도록 막는다.
    """

    @pytest.mark.asyncio
    async def test_3006_returns_false(self, monkeypatch) -> None:
        client = LotteonClient(api_key="dummy")

        async def fake_call_api(self, method, path, body=None, params=None):
            raise LotteonApiError("응답 에러 (3006): 주문의 상태를 확인해 주세요")

        monkeypatch.setattr(LotteonClient, "_call_api", fake_call_api)
        success, message = await client.seller_cancel_order(
            od_no="02682376475",
            reason_code="CC11",
            reason_text="고객 취소요청",
            od_seq=1,
            proc_seq=1,
        )
        assert success is False, (
            "3006 을 success 로 처리하면 미승인 클레임이 cancelled 로 false-success 됨"
        )
        assert "3006" in message
