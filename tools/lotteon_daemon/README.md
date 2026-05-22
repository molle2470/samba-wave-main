# LOTTEON 헤드리스 데몬 (완전 자동)

LOTTEON 가격/재고 DOM 추출 전용 로컬 PC 상주 프로세스. 자동 설치 + 자동 로그인 + 여러 PC 동시 운용 지원(round-robin).

## 자동화 범위

| 단계 | 자동/수동 |
|------|----------|
| Python venv + 의존성 + Chromium 설치 | **자동** (setup.ps1) |
| device_id 생성 (`samba-daemon-<hostname>`) | **자동** |
| 백엔드 owner_device_ids 가드 통과 | **자동** (백엔드가 `samba-daemon-` prefix 자동 허용) |
| API 키 발급 | **자동** (`/proxy/extension-key`, 캐시) |
| LOTTEON 자격증명 조회 | **자동** (`/proxy/login-credential?site_name=LOTTEON`) |
| LOTTEON 로그인 | **자동** (Playwright form fill + submit) |
| 데몬 풀 등록 (라우팅 자동 합류) | **자동** (백엔드 `_pick_lotteon_daemon_owner` 가 polling 중인 daemon device 풀에서 round-robin) |
| 세션 만료 시 재로그인 | **자동** (`login_required` 3회 연속 감지 시 재로그인) |
| 비정상 종료 재시작 | **자동** (run.ps1 supervisor) |

수동 1회: 삼바웨이브 화면에서 LOTTEON 라디오 기본 계정(아이디/비번) 1개 등록.

## 운영 위치

- **로컬 PC 전용**. VM 운영 금지 (Playwright Chromium 풀가동 시 VM api 컨테이너 SIGKILL — CLAUDE.md `[배경제거 워커 로컬PC 전용]` 동일 패턴).
- 메인 작업 PC / 전용 PC / 여러 PC 동시 가능. 백엔드는 polling 중인 daemon device 풀을 자동 round-robin.

## 설치

```powershell
git clone <repo>
cd samba-wave\tools\lotteon_daemon
.\setup.ps1
```

처리:
- Python venv 생성 → pip 의존성 설치 → Playwright Chromium 다운로드
- 자동 device_id 출력 (예: `samba-daemon-my-pc`)

## 실행

```powershell
.\run.ps1
```

- `daemon.py` 가 무한 supervisor 루프로 동작 (비정상 종료 시 5초 후 자동 재시작).
- 첫 실행 시 자동으로:
  1. `/proxy/extension-key` 호출 → API 키 캐시 저장
  2. Chromium 영속 프로필 launch
  3. LOTTEON 홈에서 로그인 상태 검증
  4. 미로그인이면 `/proxy/login-credential` 호출 → 자격증명 받음
  5. LOTTEON 로그인 페이지 form fill + submit
  6. 폴링 시작

## 옵션 (필요 시만)

- `--backend-url` (기본 `https://api.samba-wave.co.kr`)
- `--device-id` (기본 `samba-daemon-<hostname>`)
- `--profile-dir` (기본 `%USERPROFILE%\.lotteon_daemon\chromium_profile`)
- `--poll-interval` (기본 1.5)
- `--max-consecutive-fail` (기본 10)
- `--headless` (LOTTEON 봇 감지 회피 위해 기본 headed)

환경변수 동일 키: `DAEMON_BACKEND_URL`, `DAEMON_DEVICE_ID`, `DAEMON_PROFILE_DIR`, `DAEMON_POLL_INTERVAL`, `DAEMON_MAX_CONSECUTIVE_FAIL`.

## 동작 확인

1. stdout 로그: `처리 시작 req=...` → `완료 req=... 혜택가=N,NNN 옵션=K (N.Ns)` 흐름이면 정상.
2. DB:
   ```sql
   SELECT owner_device_id, status, count(*) FROM samba_sourcing_job
   WHERE site='LOTTEON' AND created_at > now() - interval '10 min'
   GROUP BY owner_device_id, status;
   ```
   각 daemon 에 잡이 분산되며 `completed` 가 압도적이면 정상.
3. 워룸 로그에 `LOTTEON 확장앱 미응답 (60s 타임아웃)` 없으면 OK.

## 트러블슈팅

| 증상 | 원인 | 조치 |
|------|------|------|
| `daemon.py` 시작 직후 종료 + Chromium 안 뜸 | `playwright install chromium` 누락 | `setup.ps1` 재실행 |
| 초기 로그인 실패 → exit 3 | LOTTEON 라디오 기본 계정 미등록 | 삼바웨이브 화면에서 LOTTEON 계정 추가 + `is_login_default=true` |
| API key 발급 실패 → exit 2 | 백엔드 `owner_device_ids` 가 `samba-daemon-` prefix 안 허용 | 백엔드 코드 (sourcing_account.py:1006) 동기화 여부 확인 |
| 자동로그인 실패 (CAPTCHA) | LOTTEON 봇 감지 | `--headless` 제거(기본 headed) / IP 변경 / 수동 1회 로그인 후 쿠키 살리기 |
| 연속 실패 임계 초과 → exit 1 | LOTTEON WAF 차단 | `--poll-interval` 증가 |

## 변경 시 주의

- DOM 추출 JS (`LOTTEON_EXTRACT_JS`) 는 `extension/background-sourcing.js` LOTTEON 분기 포팅. 양쪽 동기화.
- 로그인 셀렉터 (`LOTTEON_LOGIN_SELECTORS`) 는 `extension/background-autologin.js:242-246` 포팅. 양쪽 동기화.
- 회신 dict 필드명은 백엔드 `backend/backend/domain/samba/plugins/sourcing/lotteon.py:600-650` 가 그대로 읽음. 키 변경 금지.

## 폴백 / 즉시 롤백

- 데몬 전체 중단 + 확장앱 폴백: VM `.env` 의 `LOTTEON_DAEMON_DEVICE_IDS` 비우고 컨테이너 재시작. 백엔드가 자동으로 확장앱 흐름으로 회귀.
- DB 활성 daemon 풀이 빈 상태 + env 도 빈 상태면 자동 폴백.
