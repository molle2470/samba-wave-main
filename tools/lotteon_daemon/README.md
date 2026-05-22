# LOTTEON 헤드리스 데몬 — Zero-Install

## 사용자 흐름 (1 click 한 번)

1. 오토튠 페이지 진입 (`/samba/warroom`)
2. 데몬 미감지 → 자동으로 `.exe` 다운로드 시작 + 상단 빨간 배너 안내
3. 다운로드된 `lotteon-daemon-setup_did=<id>.exe` 더블클릭 → SmartScreen "실행"
4. 끝. 이후 평생 자동 (재부팅 시 자동 시작 + 자동 업데이트 + 자동 로그인 + 잡 처리)

전제 1회: 삼바웨이브 화면에서 LOTTEON 라디오 기본 계정(아이디/비번) 등록.

## 자동화 매트릭스

| 단계 | 자동 |
|------|------|
| `.exe` 다운로드 (오토튠 페이지가 트리거) | O |
| `%APPDATA%\samba-lotteon-daemon\` 로 self-install | O |
| `HKCU\...\Run` 자동 시작 등록 | O |
| Chromium 영속 프로필 launch | O (PyInstaller 번들된 Chromium 사용) |
| LOTTEON 자동로그인 (`/login-credential` fetch + Playwright form fill) | O |
| 잡 폴링 + PDP DOM 추출 + 회신 | O |
| 세션 만료 시 재로그인 | O |
| 신버전 감지 시 self-restart | O |

사용자 액션 = **1번 더블클릭, 평생 1회**.

## 빌더/배포자 — 본 디렉토리 사용법

### 빌드

```powershell
.\build.ps1
```

`dist\daemon.exe` 단일 파일 생성 (~250MB).

### 배포

```powershell
.\upload.ps1 -Version 1.0.1
```

R2 bucket `samba-installers` 에 업로드:
- `lotteon-daemon-setup-v1.0.1.exe` (버전 영구 보존)
- `lotteon-daemon-setup.exe` (latest, 사용자 다운로드 URL)

다운로드 URL: `https://installer.samba-wave.co.kr/lotteon-daemon-setup.exe`

업로드 후 `backend/api/v1/routers/samba/proxy/sourcing.py` 의 `LOTTEON_DAEMON_LATEST_VERSION` 값 갱신 + 백엔드 배포.

## 백엔드 변경

- `GET /proxy/lotteon-daemon/latest-version` — 데몬 self-update 트리거 (`sourcing.py`)
- `GET /proxy/lotteon-daemon/health?device_id=…` — 오토튠 페이지가 PC 별 데몬 polling 여부 확인 (`sourcing.py`)
- `sourcing_account.py::_check_owner_device` — `samba-daemon-` prefix 자동 허용

## 프론트 변경

- `frontend/src/app/samba/warroom/page.tsx`:
  - localStorage `samba-daemon-<random>` device_id 영속
  - 60초 폴링으로 health 확인 → alive=false 시 자동 `.exe` 다운로드 트리거 + 빨간 배너

## 개발자용 직접 실행 (디버깅)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install playwright httpx
playwright install chromium
python daemon.py --device-id samba-daemon-dev --headless
```

## 코드 변경 시 동기화 필수

- `LOTTEON_EXTRACT_JS` ↔ `extension/background-sourcing.js` LOTTEON 분기
- `LOTTEON_LOGIN_SELECTORS` ↔ `extension/background-autologin.js:242-246`
- 회신 dict 필드명 ↔ `backend/backend/domain/samba/plugins/sourcing/lotteon.py:600-650`

## 폴백

- R2 installer 다운로드 실패 / 데몬 빌드 사고 → 오토튠 페이지의 자동 다운로드 트리거 동작 안 함
- 데몬 운영 전체 중단 → 백엔드 `_pick_lotteon_daemon_owner` 가 None 반환 → 잡 영구 pending (확장앱 폴백 미적용)
- 진짜 응급 시 백엔드 `_pick_lotteon_daemon_owner` 임시 비활성화 (`return None`) 후 재배포
