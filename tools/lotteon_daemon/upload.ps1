# LOTTEON 데몬 .exe 를 GitHub Release 로 업로드.
# 사용: .\upload.ps1 -Version 1.0.0
#
# 사전조건:
#   - gh CLI 설치 + 인증 (gh auth login)
#   - dist\daemon.exe 존재 (.\build.ps1 로 생성)
#
# 동작:
#   - GitHub Release v<VERSION> 생성 (이미 존재하면 asset 만 갱신)
#   - lotteon-daemon-setup.exe 이름으로 asset 첨부
#   - 사용자 다운로드 URL = https://github.com/<owner>/<repo>/releases/latest/download/lotteon-daemon-setup.exe

[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$Version
)

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

$src = Join-Path $here 'dist\daemon.exe'
if (-not (Test-Path $src)) {
  Write-Host "산출물 없음: $src. 먼저 .\build.ps1 실행." -ForegroundColor Red
  exit 1
}

# 업로드용 임시 이름 — github release asset 명을 'lotteon-daemon-setup.exe' 로 고정.
$assetDir = Join-Path $here 'dist\release'
New-Item -ItemType Directory -Path $assetDir -Force | Out-Null
$assetPath = Join-Path $assetDir 'lotteon-daemon-setup.exe'
Copy-Item -Path $src -Destination $assetPath -Force

$tag = "lotteon-daemon-v$Version"
Write-Host "GitHub Release 생성/갱신: $tag"

# 기존 릴리스 있으면 자산 교체, 없으면 새로 생성
$existing = & gh release view $tag 2>$null
if ($LASTEXITCODE -eq 0) {
  Write-Host "기존 릴리스 발견 → asset 교체"
  & gh release upload $tag $assetPath --clobber
} else {
  Write-Host "신규 릴리스 생성"
  & gh release create $tag $assetPath --title "LOTTEON Daemon $Version" --notes "LOTTEON 헤드리스 데몬 $Version"
}

Write-Host ""
Write-Host "완료." -ForegroundColor Green
Write-Host "다운로드 URL: https://github.com/sbk0674-web/samba-wave/releases/latest/download/lotteon-daemon-setup.exe"
Write-Host "다음 단계: backend sourcing.py LOTTEON_DAEMON_LATEST_VERSION → '$Version' 갱신 후 배포"
