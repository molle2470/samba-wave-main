"""LOTTEON 헤드리스 데몬 — 로컬 PC 전용. 완전 자동.

자동화 흐름:
1. device_id = `samba-daemon-<hostname>` 자동 생성
2. `/proxy/extension-key` 호출 → API key 발급 (X-Device-Id whitelist 통과:
   백엔드 `_check_owner_device` 가 `samba-daemon-` prefix 허용)
3. `/proxy/login-credential?site_name=LOTTEON` 호출 → 등록된 기본 LOTTEON
   계정 username/password 평문 수신
4. Playwright Chromium 영속 프로필 launch → 쿠키 살아있으면 폴링 진입,
   아니면 LOTTEON 로그인 페이지 form fill + submit 자동
5. `/proxy/sourcing/collect-queue` polling, LOTTEON detail 잡 처리
6. 백엔드 `_pick_lotteon_daemon_owner` 가 polling 중인 daemon device 풀에서
   round-robin 으로 잡 owner 박음 → 여러 PC 동시 운용 자동

운영 위치: 로컬 PC. VM 운영 금지.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

# PyInstaller frozen 모드 — 번들된 Chromium 경로 사전 설정. playwright import 전에 set.
if getattr(sys, "frozen", False):
    _meipass = getattr(sys, "_MEIPASS", "")
    if _meipass:
        _bundled_browsers = Path(_meipass) / "playwright_browsers"
        if _bundled_browsers.exists():
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(_bundled_browsers)

import httpx
from playwright.async_api import (
    BrowserContext,
    Page,
    async_playwright,
)


# ====================================================================
# 데몬 버전 — build.ps1 가 갱신. 자동 업데이트 비교 기준.
# ====================================================================
DAEMON_VERSION = "1.0.1"


# ====================================================================
# Self-install — frozen .exe 가 1번 클릭으로 평생 작동하게 한다
# ====================================================================

_INSTALL_DIR_NAME = "samba-lotteon-daemon"
_RUN_KEY_NAME = "SambaLotteonDaemon"


def _install_dir() -> Path:
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(appdata) / _INSTALL_DIR_NAME


def _is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def _running_from_install_dir() -> bool:
    if not _is_frozen():
        return True  # 개발 모드 — install 분기 스킵
    try:
        return Path(sys.executable).resolve().parent == _install_dir().resolve()
    except Exception:
        return False


def _register_run_key(exe_path: Path) -> None:
    """HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run 에 데몬 등록.

    부팅 시 자동 시작. 실패해도 본 실행은 계속 진행한다.
    """
    if os.name != "nt":
        return
    try:
        import winreg  # type: ignore

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_SET_VALUE,
        )
        winreg.SetValueEx(
            key, _RUN_KEY_NAME, 0, winreg.REG_SZ, f'"{exe_path}"'
        )
        winreg.CloseKey(key)
        logger_print(f"Startup 등록 완료: {exe_path}")
    except Exception as exc:
        logger_print(f"Startup 등록 실패(무시): {exc}")


def _self_install_and_relaunch() -> None:
    """현재 .exe 를 %APPDATA%\\samba-lotteon-daemon\\daemon.exe 로 복사 후
    그 위치에서 detach 실행. 본 프로세스는 즉시 종료(`os._exit`).

    이미 install dir 에서 실행 중이면 호출되지 않는다.
    """
    src = Path(sys.executable).resolve()
    install_dir = _install_dir()
    install_dir.mkdir(parents=True, exist_ok=True)
    dst = install_dir / "daemon.exe"

    try:
        shutil.copy2(src, dst)
    except Exception as exc:
        logger_print(f"설치 복사 실패: {exc}")
        return

    _register_run_key(dst)

    # device_id URL 인자 / 파일명 추출 보존을 위해 원래 argv 그대로 전달
    args = sys.argv[1:]
    try:
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        creationflags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(
            [str(dst), *args],
            close_fds=True,
            creationflags=creationflags if os.name == "nt" else 0,
        )
        logger_print(f"설치 완료 → 신규 프로세스 시작: {dst}")
    except Exception as exc:
        logger_print(f"신규 프로세스 시작 실패: {exc}")

    os._exit(0)


def logger_print(msg: str) -> None:
    """frozen 초기 부트스트랩 단계 — logging 모듈 set 전이라 print 폴백."""
    try:
        print(f"[lotteon-daemon] {msg}", file=sys.stderr, flush=True)
    except Exception:
        pass

logger = logging.getLogger("lotteon-daemon")


LOTTEON_LOGIN_URL = "https://www.lotteon.com/p/member/login/common"
LOTTEON_HOME_URL = "https://www.lotteon.com/p/main"

# LOTTEON 로그인 폼 셀렉터 — extension/background-autologin.js:242-246 와 동일
LOTTEON_LOGIN_SELECTORS = {
    "id": ['#inId', 'input[name="inId"]'],
    "pw": ['#Password', 'input[type="password"]'],
    "btn": '[data-cmpnt-name="login_btn_select"]',
}


# 백엔드 LOTTEON 플러그인이 dom_ext 에서 읽는 필드. 변경 시 양쪽 동기화 필요.
LOTTEON_EXTRACT_JS = r"""
(() => {
  try {
    let isLoggedIn = false
    let _domLoginSignal = 'ambiguous'
    try {
      const memInfoEl = document.querySelector('#memInfo')
      if (memInfoEl) {
        const memInfo = JSON.parse(memInfoEl.value || '{}')
        if (memInfo && memInfo.mbNo) { isLoggedIn = true; _domLoginSignal = 'logout_link' }
        else { _domLoginSignal = 'login_link' }
      }
    } catch (_) {}
    if (_domLoginSignal === 'ambiguous') {
      const _headerText = document.querySelector('header, #header')?.innerText
        || (document.body?.innerText || '').substring(0, 300)
      if (_headerText.includes('로그인/회원가입')) { _domLoginSignal = 'login_link'; isLoggedIn = false }
      else { _domLoginSignal = 'logout_link'; isLoggedIn = true }
    }
    if (_domLoginSignal !== 'logout_link') {
      let sawLoggedOutScript = false
      for (const script of document.querySelectorAll('script')) {
        const text = script.textContent || ''
        if (!text || (!text.includes('memInfo') && !text.includes('mbNo'))) continue
        const m = text.match(/["']mbNo["']\s*:\s*["']([^"']{2,})["']/) || text.match(/\bmbNo\s*:\s*["']([^"']{2,})["']/)
        if (m && m[1]) { isLoggedIn = true; _domLoginSignal = 'logout_link'; break }
        if (/["']mbNo["']\s*:\s*(null|["']{2})/.test(text) || /\bmbNo\s*:\s*(null|["']{2})/.test(text)) {
          sawLoggedOutScript = true
        }
      }
      if (_domLoginSignal !== 'logout_link') {
        const _headerText = (
          document.querySelector('header, #header, .header, [class*="header"], nav, [class*="gnb"]')?.innerText
          || (document.body?.innerText || '').substring(0, 400)
        ).replace(/\s+/g, ' ')
        if (['로그아웃', '마이롯데', 'MY LOTTE', '주문배송'].some(t => _headerText.includes(t))) {
          isLoggedIn = true; _domLoginSignal = 'logout_link'
        } else if (_domLoginSignal !== 'login_link' && (
          ['로그인', '회원가입'].some(t => _headerText.includes(t)) || sawLoggedOutScript
        )) {
          _domLoginSignal = 'login_link'
        }
      }
    }

    let salePrice = 0, originalPrice = 0, benefitPrice = 0
    let name = '', brand = ''

    const nameEl = document.querySelector('h3[class*="product"], [class*="tit_product"], [class*="product-name"], [class*="pdp-title"]')
    name = nameEl?.textContent?.trim() || document.querySelector('meta[property="og:title"]')?.content || ''

    const brandEl = document.querySelector('[class*="brand"] a, [class*="brand-name"]')
    brand = brandEl?.textContent?.trim() || ''

    const bodyText = document.body?.innerText || ''
    const benefitMatch = bodyText.match(/([\d,]+)\s*원\s*나의\s*혜택가/)
    if (benefitMatch) benefitPrice = parseInt(benefitMatch[1].replace(/,/g, ''), 10)

    const promoMatch = bodyText.match(/(\d+)%\s+([\d,]+)\s*원/)
    if (promoMatch) salePrice = parseInt(promoMatch[2].replace(/,/g, ''), 10)

    const delEl = document.querySelector('del, s, [class*="origin"] [class*="price"], [class*="before"] [class*="price"]')
    if (delEl) {
      const delNum = delEl.textContent.replace(/[^0-9]/g, '')
      if (delNum) originalPrice = parseInt(delNum, 10)
    }
    if (!originalPrice && salePrice > 0) {
      const origMatch = bodyText.match(new RegExp((salePrice).toLocaleString() + '\\s*원\\s+([\\.\\d,]+)'))
      if (origMatch) originalPrice = parseInt(origMatch[1].replace(/[^0-9]/g, ''), 10)
    }
    if (!originalPrice) originalPrice = salePrice

    const options = []
    const sizeUl = document.querySelector('ul.selectLists[id^="select-bundleOpt-"]')
    if (sizeUl) {
      sizeUl.querySelectorAll('li').forEach(li => {
        const rawCaption = (li.querySelector('.txt, .caption')?.textContent || '').trim()
        const cleanName = rawCaption.replace(/^\[품절\]\s*/, '').replace(/\s*\(남은수량\s*\d+\)/, '').trim()
        if (!cleanName) return
        const stockText = (li.querySelector('.stock')?.textContent || '').trim()
        const isSoldOut = li.classList.contains('disabled') || stockText === '품절'
        const mStock = stockText.match(/(\d+)\s*개/)
        const mCaption = rawCaption.match(/남은수량\s*(\d+)/)
        const stock = isSoldOut ? 0 : (mStock ? parseInt(mStock[1], 10) : (mCaption ? parseInt(mCaption[1], 10) : null))
        options.push({ name: cleanName, stock, isSoldOut, raw: stockText })
      })
    }

    const images = []
    document.querySelectorAll('[class*="thumb"] img, [class*="swiper"] img, [class*="slide"] img').forEach(img => {
      let src = img.src || img.currentSrc || img.getAttribute('data-src') || ''
      if (src.startsWith('//')) src = 'https:' + src
      if (src && src.includes('http') && !src.includes('data:') && !images.includes(src)) images.push(src)
    })

    const sellerEl = document.querySelector('ul.sellerList > li.currentProduct .sellerGrade strong')
    const seller = sellerEl?.textContent?.trim() || null

    const _productInfoEl = document.querySelector('[class*="pdp"], [class*="prdInfo"], [class*="goods-info"], [class*="product-info"]')
    const _pickupArea = _productInfoEl?.innerText || bodyText.slice(0, 6000)
    const _storePickupOnly = /매장\s*픽업\s*전용/.test(_pickupArea)

    return {
      success: !!(name || salePrice > 0 || options.length > 0),
      site_product_id: window.__PRD_ID__ || '',
      name, brand,
      original_price: originalPrice,
      sale_price: salePrice || benefitPrice,
      best_benefit_price: benefitPrice,
      images: images.slice(0, 9),
      source_site: 'LOTTEON',
      category: '', category1: '', category2: '', category3: '',
      options, seller,
      pageTitle: document.title,
      store_pickup_only: _storePickupOnly,
      _loginRequired: _domLoginSignal === 'login_link',
      login_required: _domLoginSignal === 'login_link',
      _domLoginSignal,
    }
  } catch (e) {
    return { success: false, error: String(e), _domLoginSignal: 'ambiguous' }
  }
})()
"""


class DaemonState:
    def __init__(self, max_consecutive_fail: int) -> None:
        self.processed = 0
        self.succeeded = 0
        self.failed = 0
        self.consecutive_fail = 0
        self.consecutive_login_required = 0
        self.max_consecutive_fail = max_consecutive_fail
        self.started_at = time.time()

    def record_success(self) -> None:
        self.processed += 1
        self.succeeded += 1
        self.consecutive_fail = 0
        self.consecutive_login_required = 0

    def record_failure(self) -> None:
        self.processed += 1
        self.failed += 1
        self.consecutive_fail += 1

    def record_login_required(self) -> None:
        self.consecutive_login_required += 1

    def reset_login_required(self) -> None:
        self.consecutive_login_required = 0

    def should_die(self) -> bool:
        return self.consecutive_fail >= self.max_consecutive_fail


def _extract_kv_from_argv_or_exename(key: str) -> str | None:
    """`.exe` 파일명 또는 argv 에서 `<key>=<value>` 추출.

    오토튠 페이지가 설치 트리거 시 `lotteon-daemon-setup_did=…_backend=…exe`
    형태로 파일명에 박거나, 인자 `--did=…` `--backend=…` 로 전달.
    포크 호환을 위해 backend URL 도 동적 전달한다.
    """
    # 파일명 추출 — URL-safe value (영숫자, 점, 하이픈, 슬래시, 콜론, 언더스코어)
    try:
        exe_name = Path(sys.executable).name
        m = re.search(
            rf"{re.escape(key)}=([A-Za-z0-9_./:-]+?)(?:_(?:did|backend)=|\.exe$|$)",
            exe_name,
        )
        if m:
            return m.group(1)
    except Exception:
        pass
    # argv 에서 추출
    for arg in sys.argv[1:]:
        m = re.match(rf"^--?{re.escape(key)}=(.+)$", arg) or re.match(
            rf"^{re.escape(key)}=(.+)$", arg
        )
        if m:
            return m.group(1)
    return None


def _extract_did_from_argv_or_exename() -> str | None:
    return _extract_kv_from_argv_or_exename("did")


def _extract_backend_from_argv_or_exename() -> str | None:
    """파일명/argv 에서 backend URL 추출. 포크 사용자도 본인 backend 가리킬 수 있게."""
    val = _extract_kv_from_argv_or_exename("backend")
    if not val:
        return None
    # URL-encoded 값 복호화 (예: https%3A//api.example.com)
    try:
        from urllib.parse import unquote

        val = unquote(val)
    except Exception:
        pass
    # 스킴 보강
    if val and not val.startswith(("http://", "https://")):
        val = f"https://{val}"
    return val


def _default_device_id() -> str:
    host = socket.gethostname() or "unknown"
    sanitized = re.sub(r"[^a-zA-Z0-9-]", "-", host).strip("-").lower() or "unknown"
    return f"samba-daemon-{sanitized}"


# ====================================================================
# API 키 / 자격증명 부트스트랩
# ====================================================================


async def bootstrap_api_key(
    client: httpx.AsyncClient,
    backend_url: str,
    device_id: str,
    cache_path: Path,
) -> str:
    """`/proxy/extension-key` 호출하여 API key 발급. 캐시 파일 우선."""
    if cache_path.exists():
        cached = cache_path.read_text(encoding="utf-8").strip()
        if cached:
            logger.info("API key 캐시 사용: %s", cache_path)
            return cached
    logger.info("API key 발급 요청 → /proxy/extension-key")
    r = await client.post(
        f"{backend_url}/api/v1/samba/proxy/extension-key",
        headers={
            "X-Device-Id": device_id,
            "User-Agent": "lotteon-daemon/1.0",
            "Origin": "chrome-extension://lotteon-daemon",
        },
        json={"gaia_id": device_id, "email": ""},
        timeout=20.0,
    )
    if r.status_code != 200:
        raise RuntimeError(
            f"API key 발급 실패 status={r.status_code} body={r.text[:300]}"
        )
    api_key = (r.json() or {}).get("api_key", "")
    if not api_key:
        raise RuntimeError("API key 발급 응답에 api_key 없음")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(api_key, encoding="utf-8")
    logger.info("API key 발급 완료 — 캐시 저장")
    return api_key


async def fetch_lotteon_credential(
    client: httpx.AsyncClient,
    backend_url: str,
    device_id: str,
    api_key: str,
) -> dict[str, str] | None:
    """등록된 LOTTEON 기본 계정의 username/password 평문 조회."""
    r = await client.get(
        f"{backend_url}/api/v1/samba/proxy/login-credential",
        params={"site_name": "LOTTEON"},
        headers={
            "X-Device-Id": device_id,
            "X-Api-Key": api_key,
        },
        timeout=15.0,
    )
    if r.status_code == 404:
        logger.warning(
            "LOTTEON 기본 계정 미등록 — 삼바웨이브 화면에서 소싱처 계정 추가 필요"
        )
        return None
    if r.status_code != 200:
        logger.warning(
            "login-credential 실패 status=%s body=%s", r.status_code, r.text[:200]
        )
        return None
    data = r.json() or {}
    if not data.get("username") or not data.get("password"):
        return None
    return {"username": data["username"], "password": data["password"]}


# ====================================================================
# LOTTEON Playwright 로그인
# ====================================================================


async def is_lotteon_logged_in(page: Page) -> bool:
    """홈페이지 열고 #memInfo.mbNo 또는 헤더 텍스트로 로그인 확정."""
    try:
        await page.goto(LOTTEON_HOME_URL, wait_until="domcontentloaded", timeout=20_000)
    except Exception as exc:
        logger.warning("LOTTEON home 로드 실패: %s", exc)
        return False
    await page.wait_for_timeout(3_000)
    try:
        result = await page.evaluate(
            """
            () => {
              try {
                const el = document.querySelector('#memInfo')
                if (el) {
                  try {
                    const info = JSON.parse(el.value || '{}')
                    if (info && info.mbNo) return 'logged_in'
                  } catch(_) {}
                }
                const txt = (document.querySelector('header, #header, nav, [class*="gnb"]')?.innerText
                  || (document.body?.innerText || '').substring(0, 400)).replace(/\\s+/g, ' ')
                if (['로그아웃', '마이롯데', 'MY LOTTE'].some(t => txt.includes(t))) return 'logged_in'
                if (txt.includes('로그인/회원가입') || ['로그인', '회원가입'].some(t => txt.includes(t))) return 'logged_out'
                return 'unknown'
              } catch (e) { return 'unknown' }
            }
            """
        )
    except Exception as exc:
        logger.warning("login 검사 evaluate 실패: %s", exc)
        return False
    return result == "logged_in"


async def lotteon_auto_login(
    page: Page, credential: dict[str, str]
) -> bool:
    """LOTTEON 로그인 페이지 form fill + submit 자동."""
    logger.info("LOTTEON 자동로그인 시작 (계정=%s)", credential["username"][:4] + "***")
    try:
        await page.goto(
            LOTTEON_LOGIN_URL, wait_until="domcontentloaded", timeout=30_000
        )
    except Exception as exc:
        logger.warning("LOTTEON 로그인 페이지 로드 실패: %s", exc)
        return False
    await page.wait_for_timeout(2_500)

    selectors_payload = json.dumps(LOTTEON_LOGIN_SELECTORS)
    cred_payload = json.dumps(credential)
    fill_js = f"""
    (() => {{
      const sel = {selectors_payload}
      const cred = {cred_payload}
      const nativeSetter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype, 'value').set
      function pick(arr) {{
        for (const s of arr) {{
          const el = document.querySelector(s)
          if (el) return el
        }}
        return null
      }}
      const idField = pick(sel.id)
      const pwField = pick(sel.pw)
      if (!idField || !pwField) {{
        return {{ ok: false, reason: 'fields not found',
                 inputs: Array.from(document.querySelectorAll('input')).slice(0, 20).map(i => ({{id:i.id,name:i.name,type:i.type}}))
        }}
      }}
      idField.focus()
      nativeSetter.call(idField, cred.username)
      idField.dispatchEvent(new Event('input', {{ bubbles: true }}))
      idField.dispatchEvent(new Event('change', {{ bubbles: true }}))
      pwField.focus()
      nativeSetter.call(pwField, cred.password)
      pwField.dispatchEvent(new Event('input', {{ bubbles: true }}))
      pwField.dispatchEvent(new Event('change', {{ bubbles: true }}))
      const btn = document.querySelector(sel.btn)
      if (!btn) return {{ ok: false, reason: 'btn not found' }}
      btn.click()
      return {{ ok: true }}
    }})()
    """
    res = await page.evaluate(fill_js)
    if not (isinstance(res, dict) and res.get("ok")):
        logger.warning("LOTTEON 로그인 form fill 실패: %s", res)
        return False

    # 로그인 후 리다이렉트/세션 안정화 대기. 최대 15초.
    for _ in range(15):
        await page.wait_for_timeout(1_000)
        if await is_lotteon_logged_in(page):
            logger.info("LOTTEON 자동로그인 성공")
            return True
    logger.warning("LOTTEON 자동로그인 — 15초 후에도 로그인 확정 안 됨 (CAPTCHA 의심)")
    return False


async def ensure_logged_in(
    page: Page,
    client: httpx.AsyncClient,
    backend_url: str,
    device_id: str,
    api_key: str,
) -> bool:
    """로그인 상태 확인 → 미로그인 시 1회 자동로그인 시도."""
    if await is_lotteon_logged_in(page):
        logger.info("LOTTEON 세션 살아있음 — 자동로그인 스킵")
        return True
    cred = await fetch_lotteon_credential(client, backend_url, device_id, api_key)
    if not cred:
        logger.error(
            "LOTTEON 자격증명 미등록 — 삼바웨이브에서 LOTTEON 라디오 기본 계정 추가 필요"
        )
        return False
    return await lotteon_auto_login(page, cred)


# ====================================================================
# 잡 폴링 / 처리
# ====================================================================


async def fetch_job(
    client: httpx.AsyncClient, backend_url: str, device_id: str
) -> dict[str, Any] | None:
    try:
        r = await client.get(
            f"{backend_url}/api/v1/samba/proxy/sourcing/collect-queue",
            headers={
                "X-Device-Id": device_id,
                "X-Allowed-Sites": "LOTTEON",
                "X-Ext-Version": "99.0.0",
            },
            timeout=10.0,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as exc:
        logger.warning("polling 실패: %s", exc)
        return None
    if not data.get("hasJob"):
        return None
    return data


_RETRY_STATUSES = {429, 502, 503, 504}
_RETRY_DELAYS = (0.5, 1.5, 3.0)


async def post_result(
    client: httpx.AsyncClient,
    backend_url: str,
    request_id: str,
    data: dict[str, Any],
) -> bool:
    url = f"{backend_url}/api/v1/samba/proxy/sourcing/collect-result"
    body = {"requestId": request_id, "data": data}
    for attempt in range(len(_RETRY_DELAYS) + 1):
        try:
            r = await client.post(url, json=body, timeout=15.0)
        except Exception as exc:
            if attempt < len(_RETRY_DELAYS):
                await asyncio.sleep(_RETRY_DELAYS[attempt])
                continue
            logger.warning("결과전송 예외 (포기): %s", exc)
            return False
        if r.is_success:
            return True
        if r.status_code in _RETRY_STATUSES and attempt < len(_RETRY_DELAYS):
            await asyncio.sleep(_RETRY_DELAYS[attempt])
            continue
        logger.warning("결과전송 실패 status=%s body=%s", r.status_code, r.text[:200])
        return False
    return False


async def extract_lotteon_pdp(
    page: Page, url: str, product_id: str
) -> dict[str, Any]:
    await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    await page.evaluate(f"window.__PRD_ID__ = {json.dumps(product_id)}")
    await page.wait_for_timeout(5_000)
    data = await page.evaluate(LOTTEON_EXTRACT_JS)
    if (
        isinstance(data, dict)
        and not data.get("best_benefit_price")
        and (
            (data.get("sale_price") or 0) > 0
            or (data.get("options") and len(data["options"]) > 0)
        )
    ):
        await page.wait_for_timeout(3_000)
        data2 = await page.evaluate(LOTTEON_EXTRACT_JS)
        if isinstance(data2, dict) and data2.get("best_benefit_price"):
            data = data2
    return data if isinstance(data, dict) else {
        "success": False,
        "error": "evaluate 결과 비dict",
    }


async def process_job(
    page: Page,
    client: httpx.AsyncClient,
    backend_url: str,
    job: dict[str, Any],
    state: DaemonState,
) -> str | None:
    """잡 1개 처리. 반환값:
    - None: 정상 처리(성공/실패와 무관, 회신 완료)
    - "login_required": 로그인 만료 감지 → caller 가 재로그인 트리거
    """
    request_id = job.get("requestId", "")
    site = job.get("site", "")
    jtype = job.get("type", "")
    url = job.get("url", "")
    product_id = job.get("productId", "")

    if site != "LOTTEON" or jtype != "detail":
        logger.warning("범위 밖 잡 (site=%s type=%s) — 실패 회신", site, jtype)
        await post_result(
            client, backend_url, request_id,
            {"success": False, "error": f"daemon scope LOTTEON detail only (got {site}/{jtype})"},
        )
        state.record_failure()
        return None

    logger.info("처리 시작 req=%s pid=%s", request_id, product_id)
    t0 = time.time()
    try:
        data = await asyncio.wait_for(
            extract_lotteon_pdp(page, url, product_id), timeout=50.0
        )
    except asyncio.TimeoutError:
        logger.warning("PDP 추출 타임아웃 req=%s pid=%s", request_id, product_id)
        await post_result(
            client, backend_url, request_id,
            {"success": False, "error": "daemon PDP 추출 타임아웃"},
        )
        state.record_failure()
        return None
    except Exception as exc:
        logger.exception("PDP 추출 예외 req=%s pid=%s: %s", request_id, product_id, exc)
        await post_result(
            client, backend_url, request_id,
            {"success": False, "error": f"daemon 예외: {exc}"},
        )
        state.record_failure()
        return None

    ok = await post_result(client, backend_url, request_id, data)
    dt = time.time() - t0
    if ok and data.get("success"):
        bp = data.get("best_benefit_price") or 0
        nopt = len(data.get("options") or [])
        logger.info(
            "완료 req=%s pid=%s 혜택가=%s 옵션=%d (%.1fs)",
            request_id, product_id, f"{bp:,}", nopt, dt,
        )
        state.record_success()
        if data.get("login_required"):
            state.record_login_required()
            return "login_required"
        state.reset_login_required()
        return None
    # 회신 OK 였으나 success=False
    state.record_failure()
    if isinstance(data, dict) and data.get("login_required"):
        state.record_login_required()
        return "login_required"
    return None


async def run_daemon(args: argparse.Namespace) -> int:
    state = DaemonState(max_consecutive_fail=args.max_consecutive_fail)
    backend_url = args.backend_url.rstrip("/")
    profile_dir = Path(args.profile_dir).expanduser().resolve()
    profile_dir.mkdir(parents=True, exist_ok=True)
    api_key_path = profile_dir / "api_key.txt"

    logger.info(
        "데몬 시작 device_id=%s backend=%s profile=%s",
        args.device_id, backend_url, profile_dir,
    )

    async with httpx.AsyncClient() as http_client:
        # 자동 업데이트 체크 — 신버전 감지 시 즉시 종료(supervisor가 신버전 다운로드 트리거)
        if await _check_and_self_update(http_client, backend_url):
            return 10  # exit=10 → run.ps1/Run 키 재시작이 신버전 다운로드 트리거

        # API key 부트스트랩 (캐시 우선)
        try:
            api_key = await bootstrap_api_key(
                http_client, backend_url, args.device_id, api_key_path
            )
        except Exception as exc:
            logger.error("API key 부트스트랩 실패: %s", exc)
            return 2

        async with async_playwright() as pw:
            context: BrowserContext = await pw.chromium.launch_persistent_context(
                str(profile_dir),
                headless=args.headless,
                viewport={"width": 1280, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
            )
            page = context.pages[0] if context.pages else await context.new_page()

            # 시작 시 로그인 확인 + 필요 시 자동로그인
            if not await ensure_logged_in(
                page, http_client, backend_url, args.device_id, api_key
            ):
                logger.error("초기 로그인 실패 — 종료 (supervisor 재기동 유도)")
                await context.close()
                return 3

            idle_logged_at = 0.0
            while True:
                if state.should_die():
                    logger.error(
                        "연속 실패 %d 건 초과 — 종료(supervisor 재기동 유도)",
                        state.consecutive_fail,
                    )
                    await context.close()
                    return 1

                # 로그인 만료 누적 시 재로그인
                if state.consecutive_login_required >= 3:
                    logger.warning(
                        "login_required 3회 연속 — 재로그인 시도"
                    )
                    if not await ensure_logged_in(
                        page, http_client, backend_url, args.device_id, api_key
                    ):
                        logger.error("재로그인 실패 — 종료")
                        await context.close()
                        return 4
                    state.reset_login_required()

                job = await fetch_job(http_client, backend_url, args.device_id)
                if not job:
                    now = time.time()
                    if now - idle_logged_at > 30:
                        logger.info(
                            "대기 중 (processed=%d ok=%d fail=%d)",
                            state.processed, state.succeeded, state.failed,
                        )
                        idle_logged_at = now
                    await asyncio.sleep(args.poll_interval)
                    continue

                await process_job(
                    page, http_client, backend_url, job, state
                )


def _setup_logging() -> None:
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    logging.basicConfig(level=logging.INFO, format=fmt, stream=sys.stderr)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="LOTTEON 헤드리스 데몬 (완전 자동)")
    # backend URL 우선순위:
    # 1. URL/파일명/argv 의 backend= (포크 사용자가 본인 backend 가리킬 때)
    # 2. DAEMON_BACKEND_URL 환경변수
    # 3. 본 메인 default
    _backend_default = (
        _extract_backend_from_argv_or_exename()
        or os.environ.get("DAEMON_BACKEND_URL")
        or "https://api.samba-wave.co.kr"
    )
    p.add_argument(
        "--backend-url",
        default=_backend_default,
        help=(
            "백엔드 base URL. URL/파일명/--backend= 자동 추출. "
            "포크 사용자는 본인 backend 지정 가능."
        ),
    )
    # device_id 우선순위:
    # 1. URL/파일명/argv 의 did= (오토튠 페이지가 박은 값) — 최우선
    # 2. DAEMON_DEVICE_ID 환경변수
    # 3. samba-daemon-<hostname> 폴백
    _did_default = (
        _extract_did_from_argv_or_exename()
        or os.environ.get("DAEMON_DEVICE_ID")
        or _default_device_id()
    )
    p.add_argument(
        "--device-id",
        default=_did_default,
        help=(
            "이 데몬의 device_id. URL/파일명/--did= 자동 추출. "
            "백엔드 owner_device_ids 가 samba-daemon-* prefix 자동 허용."
        ),
    )
    p.add_argument(
        "--profile-dir",
        default=os.environ.get(
            "DAEMON_PROFILE_DIR",
            str(Path.home() / ".lotteon_daemon" / "chromium_profile"),
        ),
        help="Chromium 영속 프로필 디렉토리",
    )
    p.add_argument(
        "--poll-interval",
        type=float,
        default=float(os.environ.get("DAEMON_POLL_INTERVAL", "1.5")),
    )
    p.add_argument(
        "--max-consecutive-fail",
        type=int,
        default=int(os.environ.get("DAEMON_MAX_CONSECUTIVE_FAIL", "10")),
    )
    # 기본 headless=True — 사용자 PC 에 Chromium 창 안 뜸 (zero-visual).
    # WAF 차단 발생 시 --no-headless 로 수동 전환 가능.
    p.add_argument(
        "--no-headless",
        dest="headless",
        action="store_false",
        default=True,
        help="headed 모드로 전환 (LOTTEON WAF 차단 시 디버깅용).",
    )
    return p.parse_args()


async def _check_and_self_update(
    client: httpx.AsyncClient, backend_url: str
) -> bool:
    """백엔드 버전 체크 → 신버전이면 True 반환(caller 가 종료 → 재시작 시 신버전 다운로드).

    실패 시 False (현 버전 그대로 진행). 네트워크 일시 장애로 자가 종료되는 사고 방지.
    """
    try:
        r = await client.get(
            f"{backend_url}/api/v1/samba/proxy/lotteon-daemon/latest-version",
            timeout=10.0,
        )
        if r.status_code != 200:
            return False
        data = r.json() or {}
        latest = (data.get("version") or "").strip()
        if not latest or latest == DAEMON_VERSION:
            return False
        logger.info(
            "신버전 감지: 현재=%s latest=%s — 자기 종료 → 다음 시작 시 갱신",
            DAEMON_VERSION,
            latest,
        )
        return True
    except Exception as exc:
        logger.debug("버전 체크 실패(무시): %s", exc)
        return False


def main() -> int:
    # 1. frozen 모드 + install dir 아닐 때 → self-install + 재시작 후 종료
    if _is_frozen() and not _running_from_install_dir():
        logger_print(
            f"첫 실행 감지 — {_install_dir()} 로 자기 설치 후 재시작"
        )
        _self_install_and_relaunch()
        # _self_install_and_relaunch 가 os._exit(0) 호출하므로 이 라인 도달 X
        return 0

    _setup_logging()
    args = _parse_args()
    logger.info(
        "데몬 v%s 시작 (frozen=%s install_dir=%s)",
        DAEMON_VERSION,
        _is_frozen(),
        _install_dir() if _is_frozen() else "(개발 모드)",
    )
    try:
        return asyncio.run(run_daemon(args))
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt — 종료")
        return 0


if __name__ == "__main__":
    sys.exit(main())
