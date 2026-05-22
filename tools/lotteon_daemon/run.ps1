# LOTTEON 데몬 supervisor — 비정상 종료 시 자동 재시작.
# 사용: cd tools\lotteon_daemon; .\run.ps1
# 종료: PowerShell 창에서 Ctrl+C 두 번.

$ErrorActionPreference = 'Continue'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

# venv 활성화 (있으면)
$venvActivate = Join-Path $here '.venv\Scripts\Activate.ps1'
if (Test-Path $venvActivate) {
  & $venvActivate
}

while ($true) {
  Write-Host ("[supervisor] " + (Get-Date -Format 'HH:mm:ss') + " daemon.py 시작")
  & python daemon.py @args
  $code = $LASTEXITCODE
  Write-Host ("[supervisor] " + (Get-Date -Format 'HH:mm:ss') + " daemon.py 종료 exit=$code — 5초 후 재시작")
  Start-Sleep -Seconds 5
}
