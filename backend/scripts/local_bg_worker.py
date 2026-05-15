"""
Samba Wave Local BG Worker
- Polls backend every 5s for pending watermark removal jobs
- 우상단 영역이 "흰배경 + 로고" 패턴 → PIL 흰박스 (빠름, 95% 케이스)
- 그 외 (사진 컨텐츠 — Jordan/Nike 등) → rembg 전체 배경 제거 (느리지만 정확)
- Run: python local_bg_worker.py
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import os
import threading
import time
import uuid
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageStat

# ── Load bg_worker.env ───────────────────────────────────
_env_file = Path(__file__).parent / "bg_worker.env"
if _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            k, _, v = _line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

SAMBA_API_URL = os.environ.get("SAMBA_API_URL", "https://api.samba-wave.co.kr")
WORKER_TOKEN = os.environ.get("WORKER_TOKEN") or os.environ.get("BG_WORKER_TOKEN", "")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "5"))
# stuck/잘못 큐잉된 잡을 워커 단에서 즉시 skip하기 위한 화이트아웃 리스트(콤마 구분)
SKIP_JOB_IDS: set[str] = {
    s.strip() for s in os.environ.get("BG_SKIP_JOB_IDS", "").split(",") if s.strip()
}

HEADERS = {"X-Worker-Token": WORKER_TOKEN}

# ── R2 config (fetched from API at startup) ───────────────
_r2: dict = {}


# ── R2 upload ────────────────────────────────────────────
def _normalize_for_market(image_bytes: bytes) -> bytes:
    """마켓 등록 호환 사양으로 정규화: 1000x1000 미만 → 비율유지 upscale +
    정사각형 아니면 흰배경 padding. JPEG 재인코딩.

    롯데홈쇼핑 등은 대표이미지 최소 해상도 미달 시 fetch 후 placeholder 로 대체.
    """
    try:
        src = Image.open(io.BytesIO(image_bytes))
        if src.mode != "RGB":
            if src.mode in ("RGBA", "LA"):
                bg = Image.new("RGB", src.size, (255, 255, 255))
                rgba = src.convert("RGBA")
                bg.paste(rgba, mask=rgba.split()[3])
                src = bg
            else:
                src = src.convert("RGB")
        target = 1000
        W, H = src.size
        if max(W, H) < target:
            scale = target / max(W, H)
            src = src.resize((round(W * scale), round(H * scale)), Image.LANCZOS)
            W, H = src.size
        if W != H:
            side = max(W, H)
            canvas = Image.new("RGB", (side, side), (255, 255, 255))
            canvas.paste(src, ((side - W) // 2, (side - H) // 2))
            src = canvas
        out = io.BytesIO()
        src.save(out, format="JPEG", quality=92, optimize=True)
        return out.getvalue()
    except Exception as e:
        print(f"[Worker]   [normalize] 사이즈 정규화 실패, 원본 유지: {e}")
        return image_bytes


def upload_to_r2(image_bytes: bytes, filename: str) -> str | None:
    """R2 업로드 — 일시 장애 대비 3회 재시도(1.5s/3s 백오프)."""
    if not _r2.get("bucket"):
        return None
    import boto3

    image_bytes = _normalize_for_market(image_bytes)

    last_err: Exception | None = None
    for attempt in range(3):
        try:
            s3 = boto3.client(
                "s3",
                endpoint_url=f"https://{_r2['account_id']}.r2.cloudflarestorage.com",
                aws_access_key_id=_r2["access_key"],
                aws_secret_access_key=_r2["secret_key"],
                region_name="auto",
            )
            key = f"transformed/{filename}"
            # 마켓 서버(특히 롯데홈쇼핑) fetch 호환:
            # - inline + 명시적 .jpg 파일명 → 외부 fetch 시 확장자 파싱 안정화
            # - CacheControl → 마켓 캐시 친화적
            s3.put_object(
                Bucket=_r2["bucket"],
                Key=key,
                Body=image_bytes,
                ContentType="image/jpeg",
                ContentDisposition=f'inline; filename="{filename}"',
                CacheControl="public, max-age=31536000",
            )
            return f"{_r2['public_url'].rstrip('/')}/transformed/{filename}"
        except Exception as e:
            last_err = e
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
    print(f"[Worker]   [upload] R2 업로드 3회 실패: {last_err}")
    return None


# ── Watermark removal ────────────────────────────────────
# 우측 상단 워터마크 박스 비율 (이미지 가로/세로 기준)
# 0.30/0.22 — 로고가 박스 경계에 살짝 걸치는 케이스까지 안전 커버
_WM_BOX_W_RATIO = 0.30
_WM_BOX_H_RATIO = 0.22
# 영역이 이 값 이상으로 평균 RGB가 밝으면 "워터마크 없음"으로 보고 skip
# 250 — 작은 로고 한 점도 평균을 떨어뜨려 1단계 통과 못함 → 원본 그대로 반환되는 케이스 차단
_WM_NO_LOGO_THRESHOLD = 250
# "흰배경 + 작은 로고" 패턴 판정용 — near-white 픽셀(채널별 ≥230) 비율 기준
_NEAR_WHITE_CHANNEL = 230
# 0.70 — 로고가 살짝 커도 흰배경+로고 패턴으로 인정해 박스 덮음
_WHITE_BG_LOGO_RATIO = 0.70

# rembg 세션 lazy 캐시 (모델 200MB, 매 호출마다 로드 방지)
_rembg_session = None
_rembg_lock = threading.Lock()


def _get_rembg_session():
    """rembg 세션 1회 생성 후 재사용 (스레드 안전)."""
    global _rembg_session
    if _rembg_session is None:
        with _rembg_lock:
            if _rembg_session is None:
                from rembg import new_session

                _rembg_session = new_session("u2net")
                print("[Worker] rembg 세션 초기화 완료 (u2net)")
    return _rembg_session


def _is_white_background_logo(crop: Image.Image) -> bool:
    """우상단 영역이 '흰배경 + 작은 로고' 패턴인지 판정.

    near-white 픽셀(R/G/B 모두 ≥230) 비율이 75% 이상이면 → 워터마크 케이스.
    그 외(모델/사진 컨텐츠)는 rembg로 처리해야 함.
    """
    pixels = list(crop.getdata())
    if not pixels:
        return False
    near_white = sum(
        1
        for r, g, b in pixels
        if r >= _NEAR_WHITE_CHANNEL
        and g >= _NEAR_WHITE_CHANNEL
        and b >= _NEAR_WHITE_CHANNEL
    )
    return near_white / len(pixels) >= _WHITE_BG_LOGO_RATIO


def _rembg_alpha(
    small_src: Image.Image, *, use_alpha_matting: bool = True
) -> Image.Image:
    """rembg로 alpha mask 추출 (L 모드, small_src 크기). 메모리 절약형 핵심 함수.

    use_alpha_matting=False: 알파매팅 끄면 가장자리 거칠지만 안정적 + 메모리 적음.
    """
    from rembg import remove

    buf_in = io.BytesIO()
    small_src.save(buf_in, format="PNG")
    if use_alpha_matting:
        result = remove(
            buf_in.getvalue(),
            session=_get_rembg_session(),
            alpha_matting=True,
            alpha_matting_foreground_threshold=250,
            alpha_matting_background_threshold=30,
            alpha_matting_erode_size=15,
        )
    else:
        result = remove(buf_in.getvalue(), session=_get_rembg_session())
    out = Image.open(io.BytesIO(result)).convert("RGBA")
    _, _, _, a = out.split()
    # 매우 약한 alpha는 0으로 클램프 (잔잔한 회색 배경 잡티 제거)
    return a.point(lambda x: 0 if x < 20 else x)


def _sample_bg_color(src: Image.Image) -> tuple[int, int, int]:
    """원본 네 모서리 16x16 블록 median으로 배경색 추정.

    흰 배경이면 흰색, 회색/검정 스튜디오 컷이면 그 색을 반환해
    합성 시 흰박스가 튀어보이는 문제를 방지.
    """
    import numpy as np

    arr = np.array(src.convert("RGB"))
    h, w = arr.shape[:2]
    sz = max(8, min(16, h // 32, w // 32))
    corners = np.concatenate(
        [
            arr[:sz, :sz].reshape(-1, 3),
            arr[:sz, -sz:].reshape(-1, 3),
            arr[-sz:, :sz].reshape(-1, 3),
            arr[-sz:, -sz:].reshape(-1, 3),
        ],
        axis=0,
    )
    return tuple(int(c) for c in np.median(corners, axis=0))


def _composite_with_alpha(
    full_src: Image.Image, small_alpha: Image.Image
) -> Image.Image:
    """원본 해상도 RGB + 작은 alpha mask → 업스케일 후 원본 배경색으로 합성."""
    if small_alpha.size != full_src.size:
        big_alpha = small_alpha.resize(full_src.size, Image.LANCZOS)
    else:
        big_alpha = small_alpha
    rgba = Image.new("RGBA", full_src.size)
    rgba.paste(full_src.convert("RGBA"))
    rgba.putalpha(big_alpha)
    bg_color = _sample_bg_color(full_src)
    bg = Image.new("RGBA", full_src.size, (*bg_color, 255))
    return Image.alpha_composite(bg, rgba).convert("RGB")


def _alpha_edge_ratio(alpha: Image.Image) -> float:
    """alpha mask 가장자리에서 투명(=배경 제거 성공) 픽셀 비율 — 1.0에 가까울수록 깨끗."""
    w, h = alpha.size
    samples: list[int] = []
    samples.extend(alpha.getpixel((x, 0)) for x in range(0, w, max(1, w // 100)))
    samples.extend(alpha.getpixel((x, h - 1)) for x in range(0, w, max(1, w // 100)))
    samples.extend(alpha.getpixel((0, y)) for y in range(0, h, max(1, h // 100)))
    samples.extend(alpha.getpixel((w - 1, y)) for y in range(0, h, max(1, h // 100)))
    if not samples:
        return 0.0
    transparent = sum(1 for v in samples if v < 30)
    return transparent / len(samples)


def _is_bg_removed(result_bytes: bytes) -> bool:
    """결과 이미지의 가장자리가 충분히 흰색인지 검증.

    rembg 실패 시 결과 가장자리에 원본 배경 색이 그대로 남음 → 흰픽셀 비율로 판정.
    가장자리 픽셀의 85% 이상이 near-white(R/G/B≥240)면 배경제거 성공으로 간주.
    """
    try:
        result = Image.open(io.BytesIO(result_bytes)).convert("RGB")
        w, h = result.size
        # 상하단 1px + 좌우 1px = 가장자리 픽셀
        pixels: list[tuple[int, int, int]] = []
        pixels.extend(result.getpixel((x, 0)) for x in range(0, w, max(1, w // 100)))
        pixels.extend(
            result.getpixel((x, h - 1)) for x in range(0, w, max(1, w // 100))
        )
        pixels.extend(result.getpixel((0, y)) for y in range(0, h, max(1, h // 100)))
        pixels.extend(
            result.getpixel((w - 1, y)) for y in range(0, h, max(1, h // 100))
        )
        if not pixels:
            return False
        white = sum(1 for r, g, b in pixels if r >= 240 and g >= 240 and b >= 240)
        return white / len(pixels) >= 0.85
    except Exception:
        return False


def remove_watermark(image_bytes: bytes) -> bytes | None:
    """우상단 패턴에 따라 분기 — 결과는 항상 원본 해상도 유지(마켓 업로드 화질 보존):

    - 흰배경(워터마크 없음)        → 원본 그대로 jpg 저장
    - 흰배경 + 로고 패턴            → 원본에 흰박스 덮어 저장 (rembg 미사용)
    - 사진 컨텐츠(모델/배경)        → 768px로 다운스케일해 rembg alpha mask 추출 →
                                      mask를 원본 크기로 업스케일 → 원본 RGB와 흰배경 합성
    rembg 1·2차 모두 실패하면 None 반환 (원본 유지 + AI 배지 부착 방지).
    """
    src_orig = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    # 패턴 감지/rembg용 작은 사본 — 원본은 합성 시 보존
    if max(src_orig.size) > 768:
        ratio = 768 / max(src_orig.size)
        src_small = src_orig.resize(
            (int(src_orig.width * ratio), int(src_orig.height * ratio)),
            Image.LANCZOS,
        )
    else:
        src_small = src_orig

    w, h = src_small.size
    box_w = int(w * _WM_BOX_W_RATIO)
    box_h = int(h * _WM_BOX_H_RATIO)
    box_small = (w - box_w, 0, w, box_h)
    crop = src_small.crop(box_small)

    # 1) 우상단이 거의 순백 → 워터마크 없음, 원본 그대로 저장
    avg = ImageStat.Stat(crop).mean
    if all(c >= _WM_NO_LOGO_THRESHOLD for c in avg[:3]):
        buf = io.BytesIO()
        src_orig.save(buf, format="JPEG", quality=90)
        return buf.getvalue()

    # 2) 흰배경 + 로고 패턴 → 원본 좌표로 box 환산해 배경색 박스 덮음 (rembg 미사용)
    #    회색/검정 사진에 흰박스가 박히는 문제 방지 위해 원본 모서리 색을 샘플링
    if _is_white_background_logo(crop):
        W, H = src_orig.size
        box_orig = (
            W - int(W * _WM_BOX_W_RATIO),
            0,
            W,
            int(H * _WM_BOX_H_RATIO),
        )
        out = src_orig.copy()
        bg_color = _sample_bg_color(src_orig)
        ImageDraw.Draw(out).rectangle(box_orig, fill=bg_color)
        buf = io.BytesIO()
        out.save(buf, format="JPEG", quality=90)
        return buf.getvalue()

    # 3) 사진 컨텐츠 → rembg matting=False 만 사용 (matting=True는 pymatting Cholesky
    #    수치 불안정으로 무한 루프 hang 발생, Python 스레드는 C 확장 안에서 kill 불가).
    #    임계값 정책:
    #      - HIGH(0.85): 가장자리 깨끗 → 즉시 반환
    #      - MID(0.60): 가장자리 약간 거칠지만 사용
    #      - LOW(0.30): 거칠어도 원본보다 나은 케이스 (모델컷 컬러 배경)
    #      - LOW 미만: 모델 윤곽 못 잡음 → 변환 실패 (원본 유지)
    HIGH, MID, LOW = 0.85, 0.60, 0.30

    def _save_composite(alpha: Image.Image) -> bytes:
        composite = _composite_with_alpha(src_orig, alpha)
        buf = io.BytesIO()
        composite.save(buf, format="JPEG", quality=90)
        return buf.getvalue()

    try:
        alpha1 = _rembg_alpha(src_small, use_alpha_matting=False)
        ratio1 = _alpha_edge_ratio(alpha1)

        if ratio1 >= MID:
            if ratio1 < HIGH:
                print(f"[Worker]   [rembg] MID 통과 — 결과 사용 (ratio={ratio1:.2f})")
            return _save_composite(alpha1)

        if ratio1 >= LOW:
            print(
                f"[Worker]   [rembg] MID 미달이나 LOW({LOW}) 통과 — 거친 가장자리 감수 (ratio={ratio1:.2f})"
            )
            return _save_composite(alpha1)

        print(
            f"[Worker]   [rembg] 모델 윤곽 못 잡음 (ratio={ratio1:.2f}) — 변환 실패 처리"
        )
        return None
    except Exception as e:
        print(f"[Worker]   [rembg] 예외, 변환 실패 처리: {e}")
        return None


# ── Process one image URL ─────────────────────────────────
async def process_image(client: httpx.AsyncClient, url: str) -> str | None:
    """이미지 다운로드 → 배경제거 → R2 업로드.

    다운로드는 3회 재시도(1.5s/3s 백오프) — 일시적 네트워크/소싱처 장애 회복.
    """
    resp = None
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            resp = await client.get(url, timeout=30, follow_redirects=True)
            resp.raise_for_status()
            break
        except Exception as e:
            last_err = e
            resp = None
            if attempt < 2:
                await asyncio.sleep(1.5 * (attempt + 1))
    if resp is None:
        print(f"[Worker]   [download] 3회 실패 ({url[:60]}): {last_err}")
        return None

    try:
        processed = await asyncio.to_thread(remove_watermark, resp.content)
        if processed is None:
            # rembg 폴백 실패 — 원본 유지, 변환된 것으로 카운트하지 않음
            return None
        md5 = hashlib.md5(resp.content).hexdigest()[:8]
        filename = f"ai_{md5}_{uuid.uuid4().hex[:6]}.jpg"
        result_url = upload_to_r2(processed, filename)
        return result_url
    except Exception as e:
        print(f"[Worker]   [process] 처리 실패 ({url[:60]}): {e}")
        return None


# ── Process one job ──────────────────────────────────────
async def process_job(job: dict) -> None:
    job_id = job["job_id"]
    if job_id in SKIP_JOB_IDS:
        print(f"\n[Worker] ⏭ SKIP job (BG_SKIP_JOB_IDS): {job_id}")
        return
    scope: dict = job.get(
        "scope", {"thumbnail": True, "additional": False, "detail": False}
    )
    products: list[dict] = job.get("products", [])
    print(f"\n[Worker] Job start: {job_id} ({len(products)} products)")

    results = []
    cancelled = False
    async with httpx.AsyncClient(
        timeout=30,
        limits=httpx.Limits(max_connections=5, max_keepalive_connections=2),
    ) as client:

        async def _is_cancelled() -> bool:
            """잡 상태 조회 — cancelled면 즉시 중단."""
            try:
                r = await client.get(
                    f"{SAMBA_API_URL}/api/v1/samba/proxy/bg-jobs/{job_id}/status",
                    timeout=5,
                )
                if r.status_code == 200:
                    return r.json().get("status") == "cancelled"
            except Exception:
                pass
            return False

        for i, prod in enumerate(products, 1):
            # 매 상품 시작 전 취소 신호 확인
            if await _is_cancelled():
                print(f"[Worker]   ✋ 잡 취소 감지 — {i - 1}/{len(products)}에서 중단")
                cancelled = True
                break
            pid = prod["product_id"]
            images: list[str] = prod.get("images") or []
            detail_images: list[str] = prod.get("detail_images") or []

            # 처리 대상 이미지 총개수 미리 계산 (사진 인덱스 진행률 표시용)
            img_total = 0
            if scope.get("thumbnail") and images:
                img_total += 1
            if scope.get("additional") and len(images) > 1:
                img_total += len(images) - 1
            if scope.get("detail") and detail_images:
                img_total += len(detail_images)

            print(f"[Worker]   [{i}/{len(products)}] {pid} ({img_total} images)")

            new_images = list(images)
            new_detail = list(detail_images)
            transformed = 0
            img_done = 0

            async def _report_progress(
                bump_product: bool = False,
                product_result: dict | None = None,
            ) -> None:
                """사진 단위 진행률 즉시 보고 (실패 무시).

                product_result 전달 시 상품 1건의 결과를 즉시 DB에 반영하도록 요청.
                """
                try:
                    payload = {
                        "image_current": img_done,
                        "image_total": img_total,
                        "current_product_id": pid,
                        "bump_product": bump_product,
                    }
                    if product_result is not None:
                        payload["product_result"] = product_result
                    await client.patch(
                        f"{SAMBA_API_URL}/api/v1/samba/proxy/bg-jobs/{job_id}/progress",
                        headers=HEADERS,
                        json=payload,
                    )
                    # 잡 처리 중에도 heartbeat 갱신 — 모달 30초 임계 "응답없음" 오판 방지
                    try:
                        await client.patch(
                            f"{SAMBA_API_URL}/api/v1/samba/proxy/bg-jobs/heartbeat",
                            headers=HEADERS,
                            timeout=5,
                        )
                    except Exception:
                        pass
                except Exception:
                    pass

            # 새 상품 시작 즉시 image_total 보고 — 프론트 fallback("0/2"=상품 진행) 방지
            if img_total > 0:
                await _report_progress(bump_product=False)

            if scope.get("thumbnail") and images:
                url = await process_image(client, images[0])
                if url:
                    new_images[0] = url
                    transformed += 1
                img_done += 1
                await _report_progress(bump_product=False)

            if scope.get("additional") and len(images) > 1:
                for j, orig in enumerate(images[1:], 1):
                    url = await process_image(client, orig)
                    if url:
                        new_images[j] = url
                        transformed += 1
                    img_done += 1
                    await _report_progress(bump_product=False)

            if scope.get("detail") and detail_images:
                for j, orig in enumerate(detail_images):
                    url = await process_image(client, orig)
                    if url:
                        new_detail[j] = url
                        transformed += 1
                    img_done += 1
                    await _report_progress(bump_product=False)

            product_result = {
                "product_id": pid,
                "success": transformed > 0,
                "new_images": new_images,
                "new_detail_images": new_detail,
                "transformed_count": transformed,
            }
            results.append(product_result)
            print(
                f"[Worker]   [{i}/{len(products)}] {pid} -> {transformed} images done"
            )
            # 상품 1건 완료 — current 증가 + 결과 즉시 DB 반영
            await _report_progress(bump_product=True, product_result=product_result)

    if cancelled:
        # 취소된 잡: 부분 결과까지만 반영 후 종료 (백엔드 status는 이미 cancelled)
        if results:
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    await client.post(
                        f"{SAMBA_API_URL}/api/v1/samba/proxy/bg-jobs/{job_id}/complete",
                        headers=HEADERS,
                        json={"results": results, "cancelled": True},
                    )
            except Exception as e:
                print(f"[Worker] cancelled job report 실패(무시): {e}")
        print(f"[Worker] Job cancelled: {len(results)}/{len(products)} 처리됨")
        return

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{SAMBA_API_URL}/api/v1/samba/proxy/bg-jobs/{job_id}/complete",
            headers=HEADERS,
            json={"results": results},
        )
        resp.raise_for_status()
    ok = sum(1 for r in results if r["success"])
    print(f"[Worker] Job done: {ok}/{len(products)} succeeded")


# ── Fetch R2 config from API ─────────────────────────────
async def fetch_config() -> bool:
    global _r2, WORKER_TOKEN, HEADERS
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{SAMBA_API_URL}/api/v1/samba/proxy/bg-jobs/config",
                headers=HEADERS,
            )
            resp.raise_for_status()
            data = resp.json()
        if not data.get("success"):
            print(f"[Worker] Config error: {data.get('message')}")
            return False
        # 백엔드가 자동 생성한 토큰을 받아서 사용
        if data.get("worker_token"):
            WORKER_TOKEN = data["worker_token"]
            HEADERS = {"X-Worker-Token": WORKER_TOKEN}
            print("[Worker] 토큰 자동 수신 완료")
        _r2 = data["r2"]
        print(f"[Worker] R2 config loaded: bucket={_r2.get('bucket')}")
        return True
    except Exception as e:
        print(f"[Worker] Config fetch failed: {e}")
        return False


# ── Main polling loop ────────────────────────────────────
async def main() -> None:
    print("=" * 50)
    print("Samba Wave Local BG Worker")
    print(f"API: {SAMBA_API_URL}")
    print(f"Poll interval: {POLL_INTERVAL}s")
    print("Stop: Ctrl+C")
    print("=" * 50)

    print("\n[Worker] Connecting to backend...")
    if not await fetch_config():
        print("[Error] Cannot connect or token is invalid. Check WORKER_TOKEN.")
        return

    # 부팅 시 stuck running 잡 자동 정리 (이전 워커가 비정상 종료된 경우)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{SAMBA_API_URL}/api/v1/samba/proxy/bg-jobs/worker-reset-running",
                headers=HEADERS,
            )
            if r.status_code == 200:
                rj = r.json()
                cnt = rj.get("reset_count", 0)
                if cnt > 0:
                    print(f"[Worker] 부팅 stuck 잡 정리: {cnt}건 cancelled")
                else:
                    print("[Worker] stuck running 잡 없음")
    except Exception as e:
        print(f"[Worker] reset-running 실패(무시): {e}")

    while True:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                # 헬스체크 — 모달이 30초 임계로 워커 alive 판단
                try:
                    await client.patch(
                        f"{SAMBA_API_URL}/api/v1/samba/proxy/bg-jobs/heartbeat",
                        headers=HEADERS,
                        timeout=5,
                    )
                except Exception:
                    pass

                resp = await client.get(
                    f"{SAMBA_API_URL}/api/v1/samba/proxy/bg-jobs/next",
                    headers=HEADERS,
                )
                resp.raise_for_status()
                data = resp.json()

            if data.get("error"):
                print(f"\n[Worker] Auth error: {data['error']}")
                await asyncio.sleep(30)
                continue

            job = data.get("job")
            if job:
                await process_job(job)
            else:
                print(f"[Worker] Waiting... ({time.strftime('%H:%M:%S')})", end="\r")
                await asyncio.sleep(POLL_INTERVAL)

        except Exception as e:
            print(f"\n[Worker] Error: {e}")
            await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[Worker] Stopped.")
