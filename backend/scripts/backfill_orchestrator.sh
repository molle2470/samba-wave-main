#!/bin/sh
# 플레이오토 슬레이브 백필 호스트 오케스트레이터.
# 컨테이너 /tmp는 배포(컨테이너 재생성)마다 날아가므로, 커서·스크립트는
# VM 호스트에 보관하고 매 루프 docker cp 로 스크립트를 재투입한다.
# 배포로 컨테이너가 갈려도 다음 루프에서 호스트 커서로 자동 재개.

SCRIPT="$HOME/backfill_playauto_slave_sync.py"
CUR="$HOME/backfill_cursor.txt"
LOG="$HOME/backfill_orch.log"
C=samba-samba-api-1
PY=/app/backend/.venv/bin/python3
BATCH=50
MAXB=10

log() { echo "$(TZ=Asia/Seoul date '+%F %T') $*" >>"$LOG"; }

[ -f "$CUR" ] || printf '' >"$CUR"
log "=== orchestrator start (batch=$BATCH max_batches=$MAXB) ==="

while true; do
  cursor=$(cat "$CUR" 2>/dev/null)
  # 배포로 컨테이너 갈렸으면 /tmp 비어있음 → 매번 재투입
  if ! sudo docker cp "$SCRIPT" "$C:/tmp/bf.py" >/dev/null 2>&1; then
    log "docker cp 실패 (컨테이너 미준비?) — 30초 후 재시도"
    sleep 30
    continue
  fi
  out=$(sudo docker exec "$C" "$PY" /tmp/bf.py run \
        --cursor "$cursor" --batch "$BATCH" --max-batches "$MAXB" 2>>"$LOG")
  echo "$out" | grep '^PROGRESS' >>"$LOG"
  newcur=$(echo "$out" | grep '^CURSOR=' | tail -1 | cut -d= -f2)
  status=$(echo "$out" | grep '^STATUS=' | tail -1 | cut -d= -f2)
  if [ -z "$newcur" ]; then
    log "CURSOR 파싱 실패 (exec 사망/배포중?) — 20초 후 재개"
    sleep 20
    continue
  fi
  echo "$newcur" >"$CUR"
  log "cursor 저장=$newcur status=$status"
  if [ "$status" = "DONE" ]; then
    log "=== 전체 완료 cursor=$newcur ==="
    break
  fi
  sleep 3
done
log "=== orchestrator exit ==="
