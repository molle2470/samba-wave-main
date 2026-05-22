# LOTTEON 데몬 단일 .exe 빌드 — PyInstaller --onefile + Playwright Chromium 번들.
# 사용: .\build.ps1
# 산출물: dist\daemon.exe (단일 파일, ~250MB)
#
# 이 .exe 는 자기 자신을 %APPDATA%\samba-lotteon-daemon\ 로 self-install + Startup 등록.
# 사용자는 1번만 더블클릭하면 평생 자동 (자동 업데이트 포함).

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
& python --version

Write-Step "venv 생성/활성화"
if (-not (Test-Path '.venv')) { & python -m venv .venv }
& (Join-Path $here '.venv\Scripts\Activate.ps1')

Write-Step "빌드 의존성 설치"
& python -m pip install --upgrade pip
& python -m pip install playwright httpx pyinstaller

Write-Step "Playwright Chromium 다운로드 (번들 source)"
$browsersDir = Join-Path $here 'playwright_browsers'
$env:PLAYWRIGHT_BROWSERS_PATH = $browsersDir
& playwright install chromium

Write-Step "PyInstaller 빌드 (--onefile)"
if (Test-Path 'dist') { Remove-Item -Recurse -Force 'dist' }
if (Test-Path 'build') { Remove-Item -Recurse -Force 'build' }
& pyinstaller `
  --name daemon `
  --onefile `
  --noconfirm `
  --windowed `
  --add-data "$browsersDir;playwright_browsers" `
  --collect-all playwright `
  --collect-all httpx `
  --hidden-import asyncio `
  daemon.py

Write-Step "빌드 완료"
Write-Host "산출물: dist\daemon.exe"
Write-Host ""
Write-Host "배포: .\upload.ps1 (R2 업로드 + latest 키 갱신)"
