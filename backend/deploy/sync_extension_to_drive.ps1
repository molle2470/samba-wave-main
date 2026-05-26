# ============================================================
# 확장앱 → 구글드라이브 동기화
# 확장앱 코드 수정 후 1회 실행하면 repo/extension 을 구글드라이브로 미러링한다.
# 각 PC 는 이 드라이브 폴더에서 unpacked 로드 → 자가업뎃(reload)이 최신본을 읽는다.
#
# 사용법: powershell -File deploy/sync_extension_to_drive.ps1
# ============================================================

$src = "$PSScriptRoot\..\extension"
$dst = "G:\내 드라이브\extension"

if (-not (Test-Path $src)) {
  Write-Output "❌ 소스 폴더 없음: $src"
  exit 1
}

# /MIR = 미러(대상을 소스와 동일하게), /XD .git = git 폴더 제외
robocopy $src $dst /MIR /XD .git /NFL /NDL /NJH /NP

# robocopy exit code: 0~7 = 성공(복사/스킵), 8+ = 실패
if ($LASTEXITCODE -lt 8) {
  $manifest = Get-Content "$dst\manifest.json" -Raw | ConvertFrom-Json
  Write-Output "✅ 동기화 완료 — 버전 $($manifest.version) → $dst"
  Write-Output "   각 PC 의 자가업뎃이 6시간 내 자동 reload 합니다."
} else {
  Write-Output "❌ 동기화 실패 (robocopy exit $LASTEXITCODE)"
  exit 1
}
