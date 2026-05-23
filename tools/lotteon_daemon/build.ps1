# Autotune daemon single .exe build - PyInstaller --onefile + Playwright Chromium bundle.
# Usage: .\build.ps1
# Output: dist\daemon.exe (single file, ~342MB)
#
# This .exe self-installs to %APPDATA%\samba-autotune-daemon\ + registers HKCU\Run.
# User clicks once, daemon runs forever (auto-update included).

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

function Write-Step($msg) {
  Write-Host ""
  Write-Host ("=== " + $msg + " ===") -ForegroundColor Cyan
}

Write-Step "Python check"
& python --version

Write-Step "venv create/activate"
if (-not (Test-Path '.venv')) { & python -m venv .venv }
& (Join-Path $here '.venv\Scripts\Activate.ps1')

Write-Step "Install build deps"
& python -m pip install --upgrade pip
& python -m pip install playwright httpx pyinstaller pystray Pillow

Write-Step "Playwright Chromium download (bundle source)"
$browsersDir = Join-Path $here 'playwright_browsers'
$env:PLAYWRIGHT_BROWSERS_PATH = $browsersDir
& playwright install chromium

Write-Step "PyInstaller build (onefile + noconsole + tray)"
if (Test-Path 'dist') { Remove-Item -Recurse -Force 'dist' }
if (Test-Path 'build') { Remove-Item -Recurse -Force 'build' }
& pyinstaller `
  --name daemon `
  --onefile `
  --noconfirm `
  --noconsole `
  --add-data "$browsersDir;playwright_browsers" `
  --collect-all playwright `
  --collect-all httpx `
  --collect-all pystray `
  --collect-all PIL `
  --hidden-import asyncio `
  --hidden-import pystray._win32 `
  --hidden-import site_handlers `
  daemon.py

Write-Step "Build done"
Write-Host "Output: dist\daemon.exe"
Write-Host ""
Write-Host "Deploy: .\upload.ps1 (GitHub Release upload)"
