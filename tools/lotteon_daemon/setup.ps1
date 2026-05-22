# LOTTEON 데몬 완전 자동 설치 스크립트.
# 사용:
#   cd tools\lotteon_daemon
#   .\setup.ps1
#
# 처리:
#   - Python venv 생성 + 의존성 설치 (playwright, httpx)
#   - Playwright Chromium 다운로드
#   - 자동 device_id 출력
#   - 첫 실행은 사용자가 run.ps1 로 시작 (데몬 시작 시 API key 자동 발급 +
#     LOTTEON 자동로그인 — 별도 수동 단계 없음)

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

function Write-Step($msg) {
  Write-Host ""
  Write-Host ("=== " + $msg + " ===") -ForegroundColor Cyan
}

Write-Step "Python 확인"
$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
  Write-Host "python 미설치. https://www.python.org/downloads/ 에서 설치 후 재실행." -ForegroundColor Red
  exit 1
}
& python --version

Write-Step "venv 생성"
if (-not (Test-Path '.venv')) {
  & python -m venv .venv
}
$venvActivate = Join-Path $here '.venv\Scripts\Activate.ps1'
& $venvActivate

Write-Step "pip 의존성 설치"
& python -m pip install --upgrade pip
& python -m pip install playwright httpx

Write-Step "Playwright Chromium 설치"
& playwright install chromium

Write-Step "자동 device_id 확인"
$autoDid = "samba-daemon-" + (($env:COMPUTERNAME.ToLower() -replace '[^a-z0-9-]', '-').Trim('-'))
Write-Host "이 PC 자동 device_id = $autoDid" -ForegroundColor Yellow

Write-Host ""
Write-Host "설치 완료." -ForegroundColor Green
Write-Host "다음 단계:"
Write-Host "  - 삼바웨이브에서 LOTTEON 라디오 기본 계정 1개 등록 (없으면 자동로그인 실패)"
Write-Host "  - 본 PC 에서 .\run.ps1 실행 (영구 supervisor — 자동 재시작 포함)"
Write-Host ""
Write-Host "데몬은 시작 시 다음을 자동 처리:" -ForegroundColor Cyan
Write-Host "  1) /proxy/extension-key 호출 → API 키 자동 발급 (캐시)"
Write-Host "  2) /proxy/login-credential 호출 → LOTTEON 계정 fetch"
Write-Host "  3) Playwright 로 LOTTEON 자동 로그인"
Write-Host "  4) /proxy/sourcing/collect-queue 폴링 → 잡 처리"
