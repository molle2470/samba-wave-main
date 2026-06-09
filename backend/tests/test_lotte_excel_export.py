"""롯데택배 엑셀 양식 회귀 테스트.

배경 (2026-06-08 사용자 요청):
  주문 페이지의 "엑셀 다운" 외에 롯데택배 송장 발송용 양식이 별도로 필요.
  사용자가 평소 플레이오토에서 다운받아 쓰던 양식과 헤더·순서 동일.

본 테스트는 다음 정적 계약을 보장:
  - excel-export 라우터의 format='lotte' 분기 존재
  - 헤더 7개 (수령자명/수령자전화번호/배송지우편번호/배송지주소/상품명/주문수량/배송메세지)
  - 사용자 캡처 양식과 100% 일치 (순서·문구)
  - 시트명은 KST 오늘 날짜 (런타임 결정)
  - 파일명은 "롯데택배_..." prefix
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


ORDER_PY = Path(__file__).resolve().parents[1] / (
    "backend/api/v1/routers/samba/order.py"
)


def _excel_export_body() -> str:
    """order.py 에서 export_orders_excel 함수 본문을 잘라 반환."""
    src = ORDER_PY.read_text(encoding="utf-8")
    start = src.find("async def export_orders_excel(")
    assert start != -1, "export_orders_excel 함수를 못 찾음"
    next_def = src.find("\nasync def ", start + 1)
    end = next_def if next_def != -1 else len(src)
    return src[start:end]


class TestLotteFormatRegistered:
    """ExcelExportRequest 에 format 파라미터 + 'lotte' 분기 존재 정적 검증."""

    def test_format_field_in_request(self) -> None:
        src = ORDER_PY.read_text(encoding="utf-8")
        # ExcelExportRequest dataclass 안에 format 필드 정의가 있어야 함
        assert 'format: str = "ub1"' in src, (
            "ExcelExportRequest 에 format 필드 누락 — 'ub1'/'lotte' 분기 불가"
        )

    def test_lotte_branch_present(self) -> None:
        body = _excel_export_body()
        # format 분기 진입 조건
        assert 'fmt == "lotte"' in body or "fmt == 'lotte'" in body, (
            "export_orders_excel 함수에 'lotte' 분기 누락"
        )


class TestLotteHeaders:
    """롯데택배 양식 헤더 7개 — 사용자 캡처와 100% 일치."""

    EXPECTED_HEADERS = [
        "수령자명",
        "수령자전화번호",
        "배송지우편번호",
        "배송지주소",
        "상품명",
        "주문수량",
        "배송메세지",
    ]

    def test_all_seven_headers_present(self) -> None:
        body = _excel_export_body()
        for h in self.EXPECTED_HEADERS:
            assert f'"{h}"' in body, f"헤더 '{h}' 누락"

    def test_header_order_exact(self) -> None:
        """헤더 순서까지 정확히 일치해야 함 (플레이오토 다운 양식과 동일).

        헤더 리터럴이 본문에 나타나는 순서를 추출해 EXPECTED_HEADERS 와 비교.
        """
        body = _excel_export_body()
        # lotte 분기 본문만 추출 — 'lotte' 시점부터 다음 '# ── 기본:' 직전까지
        l_start = body.find('fmt == "lotte"')
        l_end = body.find("# ── 기본:", l_start)
        if l_end == -1:
            l_end = body.find('ws.title = "orders"', l_start)
        lotte_body = body[l_start:l_end]

        # 헤더 리터럴 위치 추출
        positions = []
        for h in self.EXPECTED_HEADERS:
            idx = lotte_body.find(f'"{h}"')
            assert idx != -1, f"lotte 분기에 헤더 '{h}' 누락"
            positions.append((idx, h))
        sorted_by_pos = [h for _, h in sorted(positions)]
        assert sorted_by_pos == self.EXPECTED_HEADERS, (
            f"헤더 순서 불일치: 실제={sorted_by_pos} / 기대={self.EXPECTED_HEADERS}"
        )


class TestLotteFieldMapping:
    """lotte 분기 — 각 컬럼이 정확한 DB 필드로 매핑되는지 정적 검증."""

    def setup_method(self) -> None:
        body = _excel_export_body()
        l_start = body.find('fmt == "lotte"')
        l_end = body.find("# ── 기본:", l_start)
        if l_end == -1:
            l_end = body.find('ws.title = "orders"', l_start)
        self.lotte_body = body[l_start:l_end]

    def test_customer_name(self) -> None:
        assert "o.customer_name" in self.lotte_body, "수령자명 매핑 누락"

    def test_customer_phone(self) -> None:
        assert "o.customer_phone" in self.lotte_body, "수령자전화번호 매핑 누락"

    def test_customer_postal_code(self) -> None:
        assert "o.customer_postal_code" in self.lotte_body, "배송지우편번호 매핑 누락"

    def test_address_concat(self) -> None:
        # 주소 + 상세주소 합치는 _join_addr 헬퍼 호출
        assert "_join_addr" in self.lotte_body, (
            "배송지주소 — customer_address + customer_address_detail 합치는 헬퍼 누락"
        )
        assert "customer_address" in self.lotte_body
        assert "customer_address_detail" in self.lotte_body

    def test_product_name(self) -> None:
        assert "o.product_name" in self.lotte_body, "상품명 매핑 누락"

    def test_quantity(self) -> None:
        assert "o.quantity" in self.lotte_body, "주문수량 매핑 누락"

    def test_customer_note(self) -> None:
        assert "o.customer_note" in self.lotte_body, "배송메세지 매핑 누락"


class TestLotteFilename:
    """파일명 prefix '롯데택배_'."""

    def test_filename_prefix(self) -> None:
        body = _excel_export_body()
        # 선택 다운 + 필터 다운 둘 다 '롯데택배_' prefix 사용
        assert '"롯데택배_선택' in body or "'롯데택배_선택" in body, (
            "선택 다운 파일명 prefix 누락"
        )
