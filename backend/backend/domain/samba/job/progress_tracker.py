"""잡 진행 속도(건당 처리시간) 추적 — 최근 N건 슬라이딩 윈도우.

`transmit-queue-status` 같은 폴링 엔드포인트가 누적 평균 대신
실제 최근 처리 속도를 노출하기 위함.
- 인메모리 전용 — 프로세스 재시작 시 비워짐 (DB 부담 회피)
- 샘플 부족 시 None → 프런트엔드가 누적평균으로 폴백
"""

from collections import deque
from threading import Lock
from time import monotonic

_WINDOW = 20  # 최근 20개 진행 샘플 보관
_samples: dict[str, deque] = {}
_lock = Lock()


def record_progress(job_id: str, current: int) -> None:
    """update_progress 호출마다 (monotonic_ts, current) 샘플 1개 기록."""
    if not job_id:
        return
    with _lock:
        dq = _samples.get(job_id)
        if dq is None:
            dq = deque(maxlen=_WINDOW)
            _samples[job_id] = dq
        # 동일 current 중복 기록 방지 (락 경합/재실행 등으로 같은 값 두 번 들어오는 경우)
        if dq and dq[-1][1] == current:
            return
        dq.append((monotonic(), current))


def get_recent_sec_per_item(job_id: str) -> float | None:
    """최근 윈도우 기준 건당 평균 소요시간(초). 샘플 부족 시 None."""
    if not job_id:
        return None
    with _lock:
        dq = _samples.get(job_id)
        if not dq or len(dq) < 2:
            return None
        t_old, c_old = dq[0]
        t_new, c_new = dq[-1]
    delta_c = c_new - c_old
    delta_t = t_new - t_old
    if delta_c <= 0 or delta_t <= 0:
        return None
    return delta_t / delta_c


def clear_progress(job_id: str) -> None:
    """잡 종료/실패/취소 시 호출 — 메모리 정리."""
    if not job_id:
        return
    with _lock:
        _samples.pop(job_id, None)
