from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOTTEHOME = ROOT / "backend/domain/samba/plugins/markets/lottehome.py"
SHIPMENT = ROOT / "backend/domain/samba/shipment/service.py"


def test_lottehome_plugin_requires_goods_no_and_db_save_success():
    src = LOTTEHOME.read_text(encoding="utf-8")

    assert "유효 goods_no 없음" in src
    assert "DB 연결 저장 실패" in src
    assert '"success": False' in src
    assert '"product_no": goods_no' in src
    assert "await session.rollback()" in src


def test_shipment_rejects_lottehome_success_without_goods_no_and_save_failure():
    src = SHIPMENT.read_text(encoding="utf-8")

    assert "goods_no 없는 성공 응답 차단" in src
    assert "롯데홈쇼핑 DB 연결 저장 실패" in src
    assert "성공 취소" in src
