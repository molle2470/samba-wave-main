TOK='X-Internal-Token: eAjWqVyzJpsAlk8HClUHTXh97EO3_DkMiRkyBd1BdcQZSNYe'
URL='https://api.samba-wave.co.kr/api/v1/internal/cs/pending-with-context?limit=1'
for i in $(seq 1 40); do
  code=$(curl -s -o /dev/null -w "%{http_code}" -H "$TOK" "$URL")
  if [ "$code" = "200" ]; then echo "RECOVERED after ${i} tries"; exit 0; fi
  sleep 6
done
echo "STILL_DOWN after 40 tries"; exit 1
