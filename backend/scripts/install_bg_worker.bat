@echo off
chcp 65001 > nul
setlocal EnableDelayedExpansion
echo ================================================
echo  Samba Wave Background Removal Worker - Install
echo ================================================
echo.

REM ── 표준 설치 경로 ──────────────────────────────
set "INSTALL_DIR=%LOCALAPPDATA%\SambaWave\bg-worker"
set "SRC_DIR=%~dp0"
set "WORKER=%INSTALL_DIR%\local_bg_worker.py"
set "ENVFILE=%INSTALL_DIR%\bg_worker.env"
set "WD_PS1=%INSTALL_DIR%\bg_worker_watchdog.ps1"
set "WD_VBS=%INSTALL_DIR%\bg_worker_watchdog.vbs"
set "TASK_NAME=SambaWaveBgWorker"
set "TASK_NAME_WD=SambaWaveBgWorkerWatchdog"

REM ── [1/6] Python 검사 (없으면 winget 자동 설치 시도) ─
echo [1/6] Checking Python...
where python >nul 2>nul
if errorlevel 1 (
    echo       Python not found. Trying to install via winget...
    where winget >nul 2>nul
    if errorlevel 1 (
        echo.
        echo [ERROR] Python ^& winget not available.
        echo         Install Python 3.10+ manually from https://python.org
        echo         Make sure to check "Add Python to PATH" during install.
        goto :END
    )
    winget install --id Python.Python.3.12 --silent --accept-source-agreements --accept-package-agreements
    REM PATH 갱신을 위해 새 셸 환경에서 재검사
    where python >nul 2>nul
    if errorlevel 1 (
        echo.
        echo [WARN] Python installed but PATH not yet refreshed.
        echo        Close this window and re-run install after restarting your terminal/PC.
        goto :END
    )
)
for /f "tokens=*" %%i in ('where python') do (
    set "PYTHON=%%i"
    goto :gotpython
)
:gotpython
python --version
echo       Using: !PYTHON!

REM ── [2/6] 표준 경로로 파일 복사 ─────────────────
echo.
echo [2/6] Installing files to: %INSTALL_DIR%
if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"
copy /Y "%SRC_DIR%local_bg_worker.py" "%WORKER%" > nul
if errorlevel 1 (
    echo [ERROR] local_bg_worker.py not found in package. Re-download installer.
    goto :END
)
if not exist "%ENVFILE%" (
    if exist "%SRC_DIR%bg_worker.env" copy /Y "%SRC_DIR%bg_worker.env" "%ENVFILE%" > nul
)
echo       Copied worker files.

REM ── [3/6] 의존성 설치 ───────────────────────────
echo.
echo [3/6] Installing Python packages (rembg, httpx, boto3, pillow, numpy)...
echo       This may take 2-5 minutes on first run.
python -m pip install --quiet --upgrade pip
python -m pip install --quiet --upgrade "rembg[cpu]" httpx boto3 pillow numpy
if errorlevel 1 (
    echo [ERROR] Package install failed. See error above.
    goto :END
)
echo       Done.

REM ── [4/6] 워치독 파일 복사 (.ps1 + .vbs로 cmd창 깜빡임 없이 hidden 실행) ─
echo.
echo [4/6] Installing watchdog scripts...
copy /Y "%SRC_DIR%bg_worker_watchdog.ps1" "%WD_PS1%" > nul
copy /Y "%SRC_DIR%bg_worker_watchdog.vbs" "%WD_VBS%" > nul
if errorlevel 1 (
    echo [ERROR] watchdog files missing in package. Re-download installer.
    goto :END
)
echo       Installed: %WD_PS1%
echo       Installed: %WD_VBS%

REM ── [5/6] 작업 스케줄러 등록 ────────────────────
echo.
echo [5/6] Registering Windows Scheduled Tasks...

REM 기존 작업 정리 (재설치/복구 대응)
schtasks /Delete /TN "%TASK_NAME%" /F >nul 2>nul
schtasks /Delete /TN "%TASK_NAME_WD%" /F >nul 2>nul

REM ONLOGON 메인 작업 — 권한 안되면 워치독으로 대체되니 실패해도 진행
schtasks /Create /TN "%TASK_NAME%" /TR "\"!PYTHON!\" -u \"%WORKER%\"" /SC ONLOGON /RL LIMITED /F >nul 2>nul
if errorlevel 1 (
    echo       [skip] On-logon task (admin required) - watchdog will handle boot start
) else (
    echo       Registered: %TASK_NAME% (on logon)
)

REM 1분마다 워치독 — 죽으면 자동 부활 (powershell -WindowStyle Hidden 직접 호출, conhost 깜빡임 방지)
schtasks /Create /TN "%TASK_NAME_WD%" /TR "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File \"%WD_PS1%\"" /SC MINUTE /MO 1 /RL LIMITED /F >nul
if errorlevel 1 (
    echo [ERROR] Watchdog task registration failed. Worker won't auto-restart.
    goto :END
)
echo       Registered: %TASK_NAME_WD% (every 1 minute)

REM 레거시 정리
set "OLD_STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\samba_bg_worker.bat"
if exist "%OLD_STARTUP%" del "%OLD_STARTUP%" >nul 2>nul

REM ── [6/6] 즉시 시작 ─────────────────────────────
echo.
echo [6/6] Starting worker now...
powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "%WD_PS1%"
echo       Worker launched (or already running).

echo.
echo ================================================
echo  Install complete!
echo  - Worker installed at: %INSTALL_DIR%
echo  - Running NOW in background
echo  - Auto-restart within 1 minute if it dies
echo  - Survives PC reboot (watchdog runs at boot)
echo.
echo  You can now use the "Background Removal" button
echo  on the website. This window can be closed.
echo ================================================

:END
echo.
pause
endlocal
