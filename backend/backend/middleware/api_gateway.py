"""API Gateway Key 검증 미들웨어 — 외부 앱의 무단 API 접근 차단.

Stage 2: 테넌트 키 우선 체크 → 글로벌 키 폴백.
X-Api-Key 가 DB samba_extension_key 에 등록된 키이면 tenant_id 를 주입,
없으면 기존 글로벌 키로 검증.
"""

import hashlib
import logging
import time
from typing import Optional

from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

# 키 검증을 건너뛸 경로 (health check, 루트, 인증 진입점)
# 회원가입·로그인 엔드포인트는 X-Api-Key 없이도 호출 가능해야 함
# (invite_code + rate_limit + role-based 가드로 보호)
_EXEMPT_PATHS = {
    "/",
    "/api/v1/health",
    "/api/v1/samba/sourcing-accounts/extension-key",
    "/api/v1/license/verify",
    "/api/v1/samba/users",  # 회원가입 POST (목록조회 GET은 라우터에서 require_admin)
    "/api/v1/samba/users/login",  # 로그인
    "/api/v1/auth/email/sign-up",
    "/api/v1/auth/email/login",
    "/api/v1/auth/refresh",
}

# 키 검증을 건너뛸 prefix (정적 자산 — 모델 프리셋 PNG 등)
# 화이트리스트 누락 시 프론트가 무한 재요청하며 워커 event loop 소모 → health timeout 유발
_EXEMPT_PREFIXES = (
    "/static/",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/api/v1/samba/proxy/bg-jobs/",  # bg-worker 내부 호출 — 워커 토큰으로 자체 인증
    "/api/v1/samba/proxy/autotune-daemon/",  # 데몬 health/version — 인증無 (오토튠 페이지 + 데몬 부트스트랩)
    "/api/v1/internal/cs/",  # CS 자동화 내부 API — 자체 X-Internal-Token 검증 (Claude 스케줄잡)
    "/api/v1/internal/balju/",  # 발주전자료 내부 API — 자체 X-Internal-Token 검증 (Claude 스케줄잡)
)

# 테넌트 키 캐시: key_hash → (tenant_id, is_install_token, bound_device_id, cached_until_monotonic)
# tenant_id = None 은 테넌트 미설정 키 (사용자 발급 후 JWT 에 tid 없는 경우)
# bound_device_id = None 은 첫 사용 전(TOFU 미수행) 상태
_KEY_CACHE: dict[str, tuple[Optional[str], bool, Optional[str], float]] = {}
_KEY_CACHE_TTL = 60.0  # 1분

# device_id 백필 완료된 key_hash (프로세스 메모리) — 키당 1회만 기록 시도해 핫패스 부담 최소화
_DEVICE_BACKFILLED: set[str] = set()


def _hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


async def _backfill_device_id(key_hash: str, device_id: str) -> None:
    """확장앱이 X-Device-Id 헤더로 보낸 device_id 를 키 행에 1회 백필.

    오토튠 status 의 _is_mine 판정은 samba_extension_key.device_id 로 테넌트 소유
    device 를 식별한다. 키 발급 시점에 device_id 가 비어 있던 기존 행(컬럼 추가 이전
    발급분 등)은 확장앱이 인증 요청을 보낼 때 이 함수로 채워진다.

    device_id IS NULL 인 행에만 기록 — 이미 값이 있으면 덮어쓰지 않는다(여러 PC 가
    같은 키를 공유할 때 device_id 가 PC 마다 뒤바뀌는 flip-flop 방지). 프로세스당
    key_hash 1회만 시도한다.
    """
    if key_hash in _DEVICE_BACKFILLED:
        return
    # await 전에 먼저 등록 — 동시 요청이 중복 write 하지 않도록
    _DEVICE_BACKFILLED.add(key_hash)
    try:
        from backend.db.orm import get_write_session

        async with get_write_session() as session:
            await session.execute(
                text(
                    "UPDATE samba_extension_key SET device_id = :dev "
                    "WHERE key_hash = :kh AND device_id IS NULL"
                ),
                {"dev": device_id, "kh": key_hash},
            )
            await session.commit()
    except Exception as exc:
        # 백필 실패는 요청 처리에 영향 주지 않음 — 재시도 가능하도록 set 에서 제거
        _DEVICE_BACKFILLED.discard(key_hash)
        logger.warning("[api-gateway] device_id 백필 실패: %s", exc)


async def _lookup_tenant_key(
    key_hash: str,
) -> tuple[bool, Optional[str], bool, Optional[str]]:
    """DB 에서 테넌트 키 조회. (found, tenant_id, is_install_token, bound_device_id) 반환. 1분 캐시.

    install-token(데몬 다운로드용 1시간 단기 토큰)은 만료 전이어도 exchange
    엔드포인트에서만 통과시킨다(dispatch 에서 경로 확인). 만료/revoke 된 키는 미발견 처리.

    bound_device_id: 키 행에 저장된 device_id (TOFU 로 첫 사용 시 백필). NULL 이면 아직 바인딩 안 됨.
    """
    now = time.monotonic()
    cached = _KEY_CACHE.get(key_hash)
    if cached is not None:
        tenant_id, is_install, bound_dev, expires = cached
        if now < expires:
            return True, tenant_id, is_install, bound_dev

    try:
        from backend.db.orm import get_read_session

        async with get_read_session() as session:
            result = await session.execute(
                text(
                    "SELECT tenant_id, is_install_token, device_id FROM samba_extension_key "
                    "WHERE key_hash = :kh AND revoked_at IS NULL "
                    "AND (expires_at IS NULL OR expires_at > now()) "
                    "LIMIT 1"
                ),
                {"kh": key_hash},
            )
            row = result.fetchone()

        if row is not None:
            tenant_id = row[0]
            is_install = bool(row[1])
            bound_dev = row[2]
            _KEY_CACHE[key_hash] = (
                tenant_id,
                is_install,
                bound_dev,
                now + _KEY_CACHE_TTL,
            )
            return True, tenant_id, is_install, bound_dev

        return False, None, False, None
    except Exception as exc:
        logger.warning("[api-gateway] 테넌트 키 조회 실패: %s", exc)
        return False, None, False, None


def _invalidate_key_cache(key_hash: str) -> None:
    """캐시 무효화 — device_id 백필 직후 다음 요청이 최신 값을 읽도록."""
    _KEY_CACHE.pop(key_hash, None)


class ApiGatewayMiddleware(BaseHTTPMiddleware):
    """X-Api-Key 헤더를 검증하여 허가된 클라이언트만 API 접근 허용.

    검증 순서:
    1. 테넌트 키 DB 조회 (1분 캐시) → 유효하면 request.state.tenant_id 주입 후 통과
    2. 글로벌 키 일치 여부 확인 → 일치하면 통과
    3. 둘 다 실패 → 403
    """

    def __init__(self, app, api_key: str):
        super().__init__(app)
        self.api_key = api_key

    async def dispatch(self, request: Request, call_next):
        # CORS preflight는 통과
        if request.method == "OPTIONS":
            return await call_next(request)

        # 면제 경로는 통과
        if request.url.path in _EXEMPT_PATHS:
            return await call_next(request)
        if request.url.path.startswith(_EXEMPT_PREFIXES):
            return await call_next(request)

        # 키가 설정되지 않은 경우(개발환경) 통과
        if not self.api_key:
            return await call_next(request)

        request_key = request.headers.get("X-Api-Key", "")

        # 1단계: 테넌트 키 체크 (DB 조회, 1분 캐시)
        if request_key:
            key_hash = _hash_key(request_key)
            found, tenant_id, is_install, bound_dev = await _lookup_tenant_key(key_hash)
            if found:
                # install-token 은 exchange 엔드포인트에서만 통과 — 일반 API 거부.
                if is_install and not request.url.path.endswith("/exchange"):
                    logger.warning(
                        "[api-gateway] install-token 일반 API 차단: %s",
                        request.url.path,
                    )
                    return JSONResponse(
                        status_code=403,
                        content={
                            "detail": "install-token 은 키 교환에만 사용 가능합니다."
                        },
                    )
                request.state.tenant_id = tenant_id
                # 확장앱 device_id 백필 — install-token 제외, X-Device-Id 헤더 있을 때만.
                # TOFU(Trust On First Use): bound_dev IS NULL 인 키의 첫 호출 device 로 바인딩.
                # 이후 호출은 bound_dev 와 X-Device-Id 일치 여부를 라우터 의존성에서 검증.
                if not is_install:
                    _dev = (request.headers.get("X-Device-Id") or "").strip()
                    if _dev and bound_dev is None:
                        await _backfill_device_id(key_hash, _dev)
                        _invalidate_key_cache(key_hash)
                        bound_dev = _dev
                request.state.key_bound_device_id = bound_dev
                request.state.key_hash = key_hash
                return await call_next(request)

        # 2단계: 글로벌 키 폴백 (DEPRECATE_GLOBAL_KEY=true 시 건너뜀)
        from backend.core.config import settings as _settings

        if not _settings.deprecate_global_key and request_key == self.api_key:
            request.state.tenant_id = None
            return await call_next(request)

        # 차단 로그 — docker 내부 IP (172.16.0.0/12 RFC1918) 는 caddy/proxy 헬스체크
        # 등 정상 트래픽이 X-Api-Key 없이 호출하는 케이스 다수 → 로그 노이즈. DEBUG 로 낮춤.
        # 외부 IP 의 invalid key 시도만 WARNING 유지 (진짜 차단 신호).
        _client_ip = request.client.host if request.client else "unknown"
        _is_internal = _client_ip.startswith(
            (
                "172.16.",
                "172.17.",
                "172.18.",
                "172.19.",
                "172.20.",
                "172.21.",
                "172.22.",
                "172.23.",
                "172.24.",
                "172.25.",
                "172.26.",
                "172.27.",
                "172.28.",
                "172.29.",
                "172.30.",
                "172.31.",
                "10.",
                "192.168.",
                "127.",
            )
        )
        _log_fn = logger.debug if _is_internal else logger.warning
        _log_fn(
            "[api-gateway] 차단: %s %s (IP: %s)",
            request.method,
            request.url.path,
            _client_ip,
        )
        return JSONResponse(
            status_code=403,
            content={"detail": "유효하지 않은 API 키입니다."},
        )
