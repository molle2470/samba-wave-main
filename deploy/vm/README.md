# GCE VM 배포 가이드

Cloud Run → GCE VM 마이그레이션으로 **월 ₩40만 → ₩10만** 절감.

## 📋 마이그레이션 단계

### Phase 1: 로컬 파일 준비 ✅ (완료)
- `deploy/vm/docker-compose.yml` — Caddy + API + Cloud SQL Proxy
- `deploy/vm/Caddyfile` — 자동 HTTPS 리버스 프록시
- `deploy/vm/startup.sh` — VM 최초 부팅 초기화 스크립트
- `.github/workflows/deploy-vm.yml` — GitHub Actions CI/CD

### Phase 2: VM 생성 (승인 필요)

```bash
gcloud compute instances create samba-wave-vm \
  --project=fresh-sanctuary-489804-v4 \
  --zone=asia-northeast3-a \
  --machine-type=e2-small \
  --image-family=ubuntu-2404-lts-amd64 \
  --image-project=ubuntu-os-cloud \
  --boot-disk-size=30GB \
  --boot-disk-type=pd-balanced \
  --tags=http-server,https-server \
  --metadata-from-file=startup-script=deploy/vm/startup.sh \
  --service-account=github-deploy@fresh-sanctuary-489804-v4.iam.gserviceaccount.com \
  --scopes=cloud-platform
```

방화벽 (포트 80/443 오픈):
```bash
gcloud compute firewall-rules create allow-http-https \
  --project=fresh-sanctuary-489804-v4 \
  --direction=INGRESS --action=ALLOW \
  --rules=tcp:80,tcp:443 --source-ranges=0.0.0.0/0 \
  --target-tags=http-server,https-server
```

### Phase 3: 초기 설정

1. **Cloud SQL 서비스 계정 키 생성**
   ```bash
   gcloud iam service-accounts create samba-vm-sql \
     --project=fresh-sanctuary-489804-v4
   gcloud projects add-iam-policy-binding fresh-sanctuary-489804-v4 \
     --member=serviceAccount:samba-vm-sql@fresh-sanctuary-489804-v4.iam.gserviceaccount.com \
     --role=roles/cloudsql.client
   gcloud iam service-accounts keys create sa-key.json \
     --iam-account=samba-vm-sql@fresh-sanctuary-489804-v4.iam.gserviceaccount.com
   ```

2. **VM에 파일 업로드**
   ```bash
   # 도메인을 Caddyfile에 넣고 업로드
   sed -i 's/api.samba-wave.com/YOUR_DOMAIN/' deploy/vm/Caddyfile
   gcloud compute scp deploy/vm/docker-compose.yml deploy/vm/Caddyfile sa-key.json \
     samba-wave-vm:/tmp --zone=asia-northeast3-a
   gcloud compute ssh samba-wave-vm --zone=asia-northeast3-a --command='sudo mv /tmp/{docker-compose.yml,Caddyfile,sa-key.json} /opt/samba/'
   ```

3. **환경변수 .env 배치**

   필수 환경변수 (Cloud Run 현재 기준, 2026-04-24 확인):
   ```
   # 실행 환경
   ENVIRONMENT=production
   DB_SSL_REQUIRED=false          # Cloud SQL Proxy 쓰면 false
   COMMIT_SHA=                     # 배포 시 CI가 자동 세팅
   WEB_CONCURRENCY=1
   GUNICORN_CMD_ARGS=--timeout 300 --graceful-timeout 300
   TERMINATION_GRACE_HACK=1

   # DB (Cloud SQL Proxy 통해 연결)
   WRITE_DB_HOST=cloud-sql-proxy  # docker-compose 서비스명
   WRITE_DB_PORT=5432
   WRITE_DB_NAME=railway          # 실제 프로덕션 DB명
   WRITE_DB_USER=postgres
   WRITE_DB_PASSWORD=<시크릿>
   READ_DB_HOST=cloud-sql-proxy
   READ_DB_PORT=5432
   READ_DB_NAME=railway
   READ_DB_USER=postgres
   READ_DB_PASSWORD=<시크릿>

   # 인증/시크릿
   JWT_SECRET_KEY=<시크릿>
   API_GATEWAY_KEY=<시크릿>

   # ESMPlus (지마켓/옥션 통합 API — sa2.esmplus.com)
   # 셀링툴 업체 인증: HOSTING_ID = 셀링툴 마스터 ID (예: hlccorp), SELLER 는 계정별 별도.
   # 일반 판매자 (셀링툴 미가입) 인증: HOSTING_ID = SELLER_ID 동일 ID 가능.
   #   - JWT kid = HOSTING_ID, payload.ssi = "G:{SELLER_ID}" or "A:{SELLER_ID}"
   #   - Secret 은 ESMplus 셀러센터 > 통합 API > 인증키 발급 메뉴에서 확보.
   ESMPLUS_HOSTING_ID=<마스터_ID>
   ESMPLUS_SECRET_KEY=<시크릿>
   # 운영 활성화 추가 절차:
   # 1) samba_market_account 에 market_type='gmarket'/'auction' 계정 등록 (seller_id 필수)
   # 2) ESM 측에 VM 외부 IP allowlist 등록 요청 (담당자 etapihelp@gmail.com)
   # 3) 카테고리 트리 일회성 동기화 (지마켓 5,445 + 옥션 4,921 leaf)
   # 4) Rate limit — 기본 30 req/min (분당). 운영 한도 받으면 후속 PR 에서 조정.

   # 카카오 배포 알림 (선택)
   KAKAO_API_KEY=<시크릿>
   KAKAO_REFRESH_TOKEN=<시크릿>

   # 기타
   LOTTEON_FULL_PAGING_ENABLED=1
   ```

   **주의:**
   - `PROXY_URLS`/`COLLECT_PROXY_URL`은 **불필요** — DB proxy_config 단일 소스로 전환 완료 (2026-04-24)
   - Secret 값은 Secret Manager에서 수동 조회 후 복사

4. **스택 시작**
   ```bash
   gcloud compute ssh samba-wave-vm --zone=asia-northeast3-a --command='sudo systemctl start samba-stack && sudo docker compose -f /opt/samba/docker-compose.yml logs -f samba-api'
   ```

### Phase 4: DNS/IP 전환

옵션 A — 새 도메인 사용 (다운타임 0)
- 프론트엔드 API URL을 새 도메인으로 변경
- DNS 전파 후 Cloud Run 삭제

옵션 B — 기존 NAT IP 재활용 (다운타임 5분)
```bash
# 1. Cloud Run 트래픽 0%
gcloud run services update-traffic samba-wave-api --to-revisions=PREVIOUS=100 ...

# 2. Cloud NAT 삭제 → IP 해제
gcloud compute routers nats delete samba-wave-nat --router=samba-wave-router ...

# 3. 해제된 IP를 VM에 재할당
gcloud compute instances delete-access-config samba-wave-vm --access-config-name="External NAT" ...
gcloud compute instances add-access-config samba-wave-vm --access-config-name="External NAT" --address=34.47.122.131 ...
```

### Phase 5: 정리 (1~2주 병행 운영 후)

```bash
# Cloud Run 서비스 삭제
gcloud run services delete samba-wave-api --region=asia-northeast3 ...

# Cloud Build 트리거 비활성화
gcloud builds triggers delete deploy-backend ...

# Artifact Registry 이미지 정리 (보관 1개)
# 이미 cleanup-policies 설정돼 있음
```

---

## 🔧 운영 가이드

### 로그 확인
```bash
gcloud compute ssh samba-wave-vm --zone=asia-northeast3-a
sudo docker compose -f /opt/samba/docker-compose.yml logs -f samba-api
```

### 재시작
```bash
sudo docker compose -f /opt/samba/docker-compose.yml restart samba-api
```

### 수동 배포 (GitHub Actions 없이)
```bash
sudo docker compose -f /opt/samba/docker-compose.yml pull samba-api
sudo docker compose -f /opt/samba/docker-compose.yml up -d samba-api
```

### 메모리/CPU 확인
```bash
sudo docker stats
```

### 디스크 용량 확인 (이미지 누적 주의)
```bash
sudo docker system df
sudo docker system prune -af  # 미사용 이미지 정리
```

---

## ⚠️ 주의사항

1. **Cloud SQL 연결**: VM은 Cloud SQL Auth Proxy 컨테이너로 연결. 퍼블릭 IP 직접 연결 금지.
2. **서비스계정 키**: `sa-key.json`은 절대 git 커밋 금지. `.gitignore` 확인.
3. **HTTPS 인증서**: Caddy가 자동 발급. 도메인 DNS A 레코드가 VM IP를 가리켜야 함.
4. **재부팅**: systemd가 자동 시작하지만 최초 1회는 수동으로 `sudo systemctl start samba-stack`.
5. **환경변수 변경**: `/opt/samba/.env` 수정 후 `docker compose up -d samba-api`로 재시작.
