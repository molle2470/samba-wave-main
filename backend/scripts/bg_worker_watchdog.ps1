# Samba Wave BG Worker Watchdog
# 작업 스케줄러가 1분마다 호출 — 워커가 죽어있으면 hidden 모드로 재기동
$ErrorActionPreference = 'SilentlyContinue'

$workerDir = 'C:\Users\canno\workspace\samba-wave\backend'
$python = "$workerDir\.venv\Scripts\python.exe"
$workerScript = 'scripts\local_bg_worker.py'

$running = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like '*local_bg_worker.py*' }

if (-not $running) {
    # WMI Win32_Process Create — 콘솔창 생성 없이 detached spawn. 자식은 부모 env 상속(PYTHONIOENCODING/UTF8)
    $env:PYTHONIOENCODING = 'utf-8'
    $env:PYTHONUTF8 = '1'
    $cmdLine = "`"$python`" -u $workerScript"
    $si = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ ShowWindow = [uint16]0 }
    Invoke-CimMethod -ClassName Win32_Process -MethodName Create `
        -Arguments @{
            CommandLine = $cmdLine
            CurrentDirectory = $workerDir
            ProcessStartupInformation = $si
        } | Out-Null
}
