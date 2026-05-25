"""eBay 마켓 플러그인.

eBay Inventory API (REST/JSON) 기반 상품 등록/수정.
인증: OAuth 2.0 Refresh Token → Access Token 자동 발급.
대상 마켓: EBAY_US (미국)

계정 필수 필드 (account.additional_fields):
  - appId: eBay Developer App ID
  - devId: eBay Developer Dev ID
  - certId: eBay Developer Cert ID (= Client Secret)
  - authToken: OAuth 2.0 Refresh Token

Business Policy (계정 설정 또는 samba_settings):
  - fulfillmentPolicyId: 배송 정책 ID
  - paymentPolicyId: 결제 정책 ID
  - returnPolicyId: 반품 정책 ID
  - merchantLocationKey: 재고 위치 키
  - exchangeRate: KRW → USD 환율 (기본 1400)
"""

from __future__ import annotations

import asyncio
import hashlib
import io
from functools import partial
from typing import Any

import httpx

from backend.domain.samba.plugins.market_base import MarketPlugin
from backend.utils.logger import logger

# 무신사 CDN 등 Referer 필요 도메인
_REFERER_MAP = {
    "msscdn.net": "https://www.musinsa.com/",
    "kream.co.kr": "https://kream.co.kr/",
}


async def _upload_images_to_r2(
    session, images: list[str], prefix: str = "ebay"
) -> list[str]:
    """이미지 URL 리스트를 R2에 업로드 후 공개 URL 리스트 반환.

    eBay는 이미지 URL에 직접 접근하므로, 핫링크 차단 CDN의 이미지는
    R2에 업로드해서 공개 URL로 교체해야 한다.
    """
    from backend.domain.samba.image.service import ImageTransformService

    img_svc = ImageTransformService(session)
    r2 = await img_svc._get_r2_client()
    if not r2:
        logger.warning("[eBay] R2 클라이언트 없음 — 이미지 원본 URL 사용")
        return images

    client, bucket_name, public_url = r2
    sem = asyncio.Semaphore(4)
    result_urls: list[str] = []

    async def _upload_one(url: str) -> str:
        if not url:
            return ""
        # 프로토콜 보정
        if url.startswith("//"):
            url = "https:" + url

        # 이미 R2 URL이면 스킵
        if public_url and public_url in url:
            return url

        # 무신사 이미지: eBay 최소 해상도(500x500) 충족 위해 더 큰 사이즈 시도
        # _125.jpg/_250.jpg/_500.jpg → _1100.jpg
        original_url = url
        if "msscdn.net" in url:
            import re

            url = re.sub(r"_(125|250|500)\.(jpg|png|webp)", r"_1100.\2", url)

        # Referer 설정
        headers: dict[str, str] = {}
        for domain, referer in _REFERER_MAP.items():
            if domain in url:
                headers["Referer"] = referer
                break

        async def _download(u: str) -> bytes | None:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as hc:
                try:
                    r = await hc.get(u, headers=headers)
                    if r.status_code != 200:
                        return None
                    if len(r.content) < 1000:
                        return None
                    return r.content
                except Exception:
                    return None

        async with sem:
            try:
                # 1차: 1100px 시도
                img_bytes = await _download(url)
                # 2차: 실패 시 500px 원본 재시도
                if img_bytes is None and url != original_url:
                    logger.info("[eBay] 1100px 실패, 500px 폴백: %s", original_url[:60])
                    img_bytes = await _download(original_url)
                # 둘 다 실패 → 해당 이미지 드롭 (원본 URL 사용하지 않음)
                if img_bytes is None:
                    logger.warning(
                        "[eBay] 이미지 다운로드 실패 — 드롭: %s", original_url[:60]
                    )
                    return ""

                # R2 업로드 (URL 확장자 기반 content-type)
                ext = "jpg"
                content_type = "image/jpeg"
                lower_url = original_url.lower()
                if ".png" in lower_url:
                    ext = "png"
                    content_type = "image/png"
                elif ".webp" in lower_url:
                    ext = "webp"
                    content_type = "image/webp"
                filename = (
                    f"{prefix}_{hashlib.md5(url.encode()).hexdigest()[:12]}.{ext}"
                )
                key = f"ebay/{filename}"

                await asyncio.to_thread(
                    partial(
                        client.upload_fileobj,
                        io.BytesIO(img_bytes),
                        bucket_name,
                        key,
                        ExtraArgs={"ContentType": content_type},
                    )
                )
                r2_url = f"{public_url}/{key}"
                logger.info("[eBay] R2 업로드 완료: %s", r2_url[:80])
                return r2_url
            except Exception as e:
                logger.warning("[eBay] R2 업로드 실패: %s — %s", url[:60], e)
                return url

    tasks = [_upload_one(u) for u in images]
    result_urls = await asyncio.gather(*tasks)
    return list(result_urls)


async def _translate_to_english(name: str, brand: str = "", session=None) -> str:
    """한글 상품명을 eBay US용 영문으로 번역 (Claude API).

    API 키 우선순위: DB(samba_settings.claude) → 환경변수(ANTHROPIC_API_KEY)
    """
    import anthropic

    from backend.core.config import settings

    # DB에서 Claude API 키 조회 (설정 페이지에서 입력한 값)
    api_key = ""
    if session:
        claude_settings = await _get_setting(session, "claude")
        if claude_settings and isinstance(claude_settings, dict):
            api_key = claude_settings.get("apiKey", "")

    # DB에 없으면 환경변수에서
    if not api_key:
        api_key = settings.anthropic_api_key

    if not api_key:
        logger.warning("[eBay] Claude API 키 없음 — 번역 스킵")
        return ""

    prompt = f"""Translate this Korean product name to English for eBay US listing.
Brand (Korean): {brand}
Korean product name: {name}

Rules:
- Convert Korean brand name to its official English name (e.g. 밀레→Millet, 푸마→PUMA, 나이키→Nike, 아디다스→Adidas, 뉴발란스→New Balance)
- Keep model numbers/codes as-is
- Remove unnecessary brackets like [공식], [정품] etc.
- Natural English product title, max 80 chars
- No quotes, just the translated title
- If you don't know the brand, just transliterate it
- NEVER explain or apologize, ONLY output the translated title"""

    client = anthropic.AsyncAnthropic(api_key=api_key)
    resp = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=100,
        messages=[{"role": "user", "content": prompt}],
    )
    translated = resp.content[0].text.strip()[:80]

    # 거부 응답 검증 — 설명/사과/한글 포함 시 원본 이름 반환
    lower = translated.lower()
    bad_patterns = [
        "i cannot",
        "i can't",
        "i'm unable",
        "sorry",
        "apologize",
        "i need to",
        "i should",
        "this appears",
        "note:",
        "however",
        "지식",
        "없습니다",
        "확인",
        "공식",
        "참고",
        "unfortunately",
        "clarif",
        "based on",
        "please",
    ]
    if any(p in lower for p in bad_patterns):
        logger.warning(
            "[eBay] 상품명 번역 거부 응답: '%s' → 원본 유지", translated[:40]
        )
        return ""
    # 한글이 5자 이상이면 번역 실패로 간주
    kr_count = sum(1 for c in translated if "가" <= c <= "힣")
    if kr_count >= 5:
        logger.warning(
            "[eBay] 상품명 번역 한글 잔존: '%s' → 원본 유지", translated[:40]
        )
        return ""

    return translated


async def _get_setting(session, key: str) -> Any:
    """samba_settings 테이블에서 설정값 조회 후 즉시 커밋 — idle in transaction 방지."""
    from backend.domain.samba.forbidden.model import SambaSettings
    from sqlmodel import select

    stmt = select(SambaSettings).where(SambaSettings.key == key)
    result = await session.execute(stmt)
    row = result.scalars().first()
    val = row.value if row else None
    try:
        await session.commit()
    except Exception:
        pass
    return val


class EbayPlugin(MarketPlugin):
    market_type = "ebay"
    policy_key = "eBay"
    required_fields = ["name", "sale_price"]

    def transform(self, product: dict, category_id: str, **kwargs) -> dict:
        """상품 데이터 → eBay Inventory API 포맷 변환."""
        from backend.domain.samba.proxy.ebay import EbayClient

        return EbayClient.transform_product(product, category_id, **kwargs)

    async def execute(
        self,
        session,
        product: dict,
        creds: dict,
        category_id: str,
        account,
        existing_no: str,
    ) -> dict[str, Any]:
        """eBay 상품 등록/수정 — 전체 로직."""
        from backend.domain.samba.proxy.ebay import EbayClient, EbayApiError

        # 계정 필드 추출
        extras: dict[str, Any] = {}
        if account:
            extras = getattr(account, "additional_fields", None) or {}

        app_id = (
            creds.get("clientId")
            or creds.get("appId")
            or extras.get("clientId")
            or extras.get("appId", "")
        )
        dev_id = creds.get("devId") or extras.get("devId", "")
        cert_id = (
            creds.get("clientSecret")
            or creds.get("certId")
            or extras.get("clientSecret")
            or extras.get("certId", "")
        )
        refresh_token = (
            creds.get("oauthToken")
            or creds.get("authToken")
            or extras.get("oauthToken")
            or extras.get("authToken", "")
        )

        if not all([app_id, cert_id, refresh_token]):
            return {
                "success": False,
                "message": "eBay 계정 정보가 불완전합니다. appId, certId, authToken(Refresh Token)을 확인해주세요.",
            }

        # Business Policy ID — 계정 extras 또는 samba_settings에서 조회.
        # (2026-05-25) resolver 위임 — find_default('ebay') 우선, 없으면 store_ebay 폴백.
        settings_creds: dict[str, Any] = {}
        if session:
            from backend.domain.samba.account.resolver import resolve_market_creds

            raw = await resolve_market_creds(
                session, None, market_type="ebay", store_key="store_ebay"
            )
            if raw and isinstance(raw, dict):
                settings_creds = raw

        fulfillment_policy_id = extras.get("fulfillmentPolicyId") or settings_creds.get(
            "fulfillmentPolicyId", ""
        )
        payment_policy_id = extras.get("paymentPolicyId") or settings_creds.get(
            "paymentPolicyId", ""
        )
        return_policy_id = extras.get("returnPolicyId") or settings_creds.get(
            "returnPolicyId", ""
        )
        merchant_location_key = extras.get("merchantLocationKey") or settings_creds.get(
            "merchantLocationKey", ""
        )
        # 환율: 1순위 설정페이지 exchange_rates(USD effectiveRate) → 2순위 store_ebay → 3순위 1400
        exchange_rate = 1400.0
        try:
            from backend.domain.samba.exchange_rate_service import (
                build_exchange_rate_response,
                get_exchange_rate_settings,
                get_latest_exchange_rates,
            )

            tenant_id = product.get("tenant_id") if isinstance(product, dict) else None
            er_settings = await get_exchange_rate_settings(session, tenant_id)
            er_latest = await get_latest_exchange_rates()
            er_resp = build_exchange_rate_response(er_settings, er_latest)
            usd_info = er_resp.get("currencies", {}).get("USD", {}) or {}
            eff_rate = float(usd_info.get("effectiveRate") or 0)
            if eff_rate > 0:
                exchange_rate = eff_rate
                logger.info(
                    "[eBay] 환율 설정 사용: USD=%.2f KRW (fixed=%s, adjustment=%s)",
                    eff_rate,
                    usd_info.get("useFixed"),
                    usd_info.get("adjustment"),
                )
            else:
                raise ValueError("effectiveRate <= 0")
        except Exception as e:
            logger.warning("[eBay] exchange_rate_service 실패 → store_ebay 폴백: %s", e)
            exchange_rate = float(
                extras.get("exchangeRate") or settings_creds.get("exchangeRate", 1400)
            )

        client = EbayClient(
            app_id=app_id,
            dev_id=dev_id,
            cert_id=cert_id,
            refresh_token=refresh_token,
        )

        # 정책 ID가 비어있으면 eBay API에서 자동 조회 (첫 번째 정책 사용)
        if not fulfillment_policy_id:
            try:
                fps = await client.get_fulfillment_policies()
                if fps:
                    fulfillment_policy_id = fps[0].get("fulfillmentPolicyId", "")
                    logger.info("[eBay] 배송정책 자동 조회: %s", fulfillment_policy_id)
            except Exception as e:
                logger.warning("[eBay] 배송정책 조회 실패: %s", e)
        if not payment_policy_id:
            try:
                pps = await client.get_payment_policies()
                if pps:
                    payment_policy_id = pps[0].get("paymentPolicyId", "")
                    logger.info("[eBay] 결제정책 자동 조회: %s", payment_policy_id)
            except Exception as e:
                logger.warning("[eBay] 결제정책 조회 실패: %s", e)
        if not return_policy_id:
            try:
                rps = await client.get_return_policies()
                if rps:
                    return_policy_id = rps[0].get("returnPolicyId", "")
                    logger.info("[eBay] 반품정책 자동 조회: %s", return_policy_id)
            except Exception as e:
                logger.warning("[eBay] 반품정책 조회 실패: %s", e)
        if not merchant_location_key:
            merchant_location_key = "DEFAULT"

        # 영문 상품명 자동 번역 (name_en 비어있으면 Claude API 사용)
        if not product.get("name_en") and product.get("name") and session:
            product = dict(product)  # 원본 변경 방지
            try:
                translated = await _translate_to_english(
                    product["name"], product.get("brand", ""), session=session
                )
                if translated:
                    product["name_en"] = translated
                    # DB에도 저장 (다음 전송 시 재사용 + UI 표시)
                    product_id = product.get("id", "")
                    if product_id:
                        from backend.domain.samba.collector.model import (
                            SambaCollectedProduct,
                        )
                        from sqlmodel import select

                        stmt = select(SambaCollectedProduct).where(
                            SambaCollectedProduct.id == product_id
                        )
                        result = await session.execute(stmt)
                        row = result.scalars().first()
                        if row:
                            row.name_en = translated
                            # market_names["eBay"]에도 저장 (상품카드 UI 표시용)
                            mnames = row.market_names or {}
                            if isinstance(mnames, dict):
                                mnames["eBay"] = translated
                                row.market_names = mnames
                            session.add(row)
                            await session.flush()
                    logger.info(
                        "[eBay] 영문 번역: %s → %s",
                        product["name"][:30],
                        translated[:50],
                    )
            except Exception as e:
                logger.warning("[eBay] 영문 번역 실패: %s", e)

        # 한글 속성 값을 영문으로 자동 변환 (color/material/origin/sex/brand/manufacturer)
        if session:
            try:
                from backend.domain.samba.ebay_mapping.service import (
                    SambaEbayMappingService,
                )

                map_svc = SambaEbayMappingService(session)
                product = dict(product)  # 원본 변경 방지

                for kr_field, category in [
                    ("color", "color"),
                    ("material", "material"),
                    ("origin", "origin"),
                    ("sex", "sex"),
                    ("brand", "brand"),
                    ("manufacturer", "brand"),  # manufacturer도 brand 매핑 사용
                ]:
                    kr_val = product.get(kr_field)
                    if kr_val:
                        en_val = await map_svc.translate(category, kr_val)
                        if en_val and en_val != kr_val:
                            product[kr_field] = en_val
                            logger.info(
                                "[eBay] %s 번역: %s → %s", kr_field, kr_val, en_val
                            )
            except Exception as e:
                logger.warning("[eBay] 속성 매핑 실패: %s", e)

        # 이미지 처리: 원본 CDN URL을 eBay에 그대로 전달
        # eBay는 외부 URL을 자체 CDN(ebayimg.com)으로 자동 캐싱하므로
        # 별도 업로드 불필요. msscdn 등 원본이 정상 동작함.

        # 데이터 변환
        data = EbayClient.transform_product(
            product,
            category_id,
            fulfillment_policy_id=fulfillment_policy_id,
            payment_policy_id=payment_policy_id,
            return_policy_id=return_policy_id,
            merchant_location_key=merchant_location_key,
            exchange_rate=exchange_rate,
        )

        try:
            if existing_no:
                # 기존 상품 수정: existing_no = listingId (offer 조회에 SKU 사용)
                data["existing_offer_id"] = ""  # SKU로 자동 조회
                result = await client.update_product(data)
                return {
                    "success": True,
                    "message": "eBay 수정 성공",
                    "data": result,
                    "product_no": existing_no,
                }
            else:
                # 신규 등록
                result = await client.register_product(
                    data,
                    fulfillment_policy_id=fulfillment_policy_id,
                    payment_policy_id=payment_policy_id,
                    return_policy_id=return_policy_id,
                    merchant_location_key=merchant_location_key,
                )
                listing_id = result.get("listingId", "")
                return {
                    "success": True,
                    "message": "eBay 등록 성공",
                    "data": result,
                    "product_no": listing_id,
                }
        except EbayApiError as e:
            action = "수정" if existing_no else "등록"
            logger.error("[eBay] %s 실패: %s | errors=%s", action, e, e.errors)
            return {"success": False, "message": f"eBay {action} 실패: {e}"}
        except Exception as e:
            action = "수정" if existing_no else "등록"
            logger.error("[eBay] %s 예외: %s", action, e)
            return {"success": False, "message": f"eBay {action} 오류: {e}"}
