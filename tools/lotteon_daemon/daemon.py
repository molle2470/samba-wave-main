"""오토튠 헤드리스 데몬 — 로컬 PC 전용. 완전 자동.

지원 사이트:
- LOTTEON: 로그인 필수
- ABCmart/GrandStage: 로그인 필수(best_benefit_price 정확성)
- SSG: 로그인 불필요, 임직원 alert 자동 dismiss

`--sites=LOTTEON,ABCmart,SSG` CLI 인자로 멀티 사이트 동시 처리.

자동화 흐름:
1. device_id = `samba-daemon-<hostname>` 자동 생성
2. `/proxy/extension-key` 호출 → API key 발급
3. requires_login 사이트 각각 `/proxy/login-credential?site_name=<site>` → 자동 로그인
4. Playwright Chromium 영속 프로필 launch → 쿠키 살아있으면 폴링 진입
5. `/proxy/sourcing/collect-queue` polling, X-Allowed-Sites 사이트 detail 처리
6. 백엔드 `pick_daemon_owner(site)` (`daemon_pool.py`) 가 polling 중인 daemon
   풀에서 site 별 round-robin 으로 잡 owner 박음 → 여러 PC 동시 운용 자동

운영 위치: 로컬 PC. VM 운영 금지.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import logging.handlers
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

# Windows: subprocess.Popen 자식이 새 콘솔창 열지 않도록.
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200
DETACHED_PROCESS = 0x00000008

# PyInstaller frozen 모드 — 번들된 Chromium 경로 사전 설정. playwright import 전에 set.
if getattr(sys, "frozen", False):
    _meipass = getattr(sys, "_MEIPASS", "")
    if _meipass:
        _bundled_browsers = Path(_meipass) / "playwright_browsers"
        if _bundled_browsers.exists():
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(_bundled_browsers)

import httpx  # noqa: E402  # playwright env 설정 후 import 필수
from playwright.async_api import (  # noqa: E402
    BrowserContext,
    Page,
    async_playwright,
)

# 사이트 핸들러 레지스트리 (ABCmart/GrandStage/SSG). LOTTEON 은 본 파일 하단 등록.
try:
    from site_handlers import SITE_HANDLERS, SiteHandler  # type: ignore
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent))
    from site_handlers import SITE_HANDLERS, SiteHandler  # type: ignore


# ====================================================================
# 데몬 버전 — build.ps1 가 갱신. 자동 업데이트 비교 기준.
# ====================================================================
DAEMON_VERSION = "1.1.0"


# ====================================================================
# Self-install — frozen .exe 가 1번 클릭으로 평생 작동하게 한다
# ====================================================================

_INSTALL_DIR_NAME = "samba-autotune-daemon"
_RUN_KEY_NAME = "SambaAutotuneDaemon"


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

    로그온 시 자동 시작. admin 권한 불필요.
    재시작 안정성 = 내장 supervisor (_supervisor_loop) 가 child worker 감시.
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
        winreg.SetValueEx(key, _RUN_KEY_NAME, 0, winreg.REG_SZ, f'"{exe_path}"')
        winreg.CloseKey(key)
        logger_print(f"Run 키 등록 완료: {exe_path}")
    except Exception as exc:
        logger_print(f"Run 키 등록 실패(무시): {exc}")


def _self_install_and_relaunch() -> None:
    """현재 .exe 를 %APPDATA%\\samba-autotune-daemon\\daemon.exe 로 복사 후
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
        creationflags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
        subprocess.Popen(
            [str(dst), *args],
            close_fds=True,
            creationflags=creationflags if os.name == "nt" else 0,
        )
        logger_print(f"설치 완료 → 신규 프로세스 시작: {dst}")
    except Exception as exc:
        logger_print(f"신규 프로세스 시작 실패: {exc}")

    os._exit(0)


def _log_file_path() -> Path:
    return _install_dir() / "daemon.log"


def logger_print(msg: str) -> None:
    """frozen 초기 부트스트랩 단계 — logging 모듈 set 전 파일 폴백.

    --noconsole 빌드라 stderr 콘솔이 없어 stderr 출력은 사라짐. 파일에 직접 append.
    """
    line = f"[autotune-daemon] {msg}\n"
    try:
        log_path = _log_file_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as fp:
            fp.write(line)
    except Exception:
        pass
    # 디버그용 — 콘솔 있으면 표시(--console 빌드 또는 .py 실행 시)
    try:
        sys.stderr.write(line)
        sys.stderr.flush()
    except Exception:
        pass


logger = logging.getLogger("autotune-daemon")


LOTTEON_LOGIN_URL = "https://www.lotteon.com/p/member/login/common"
LOTTEON_HOME_URL = "https://www.lotteon.com/p/main"

# LOTTEON 로그인 폼 셀렉터 — extension/background-autologin.js:242-246 와 동일
LOTTEON_LOGIN_SELECTORS = {
    "id": ["#inId", 'input[name="inId"]'],
    "pw": ["#Password", 'input[type="password"]'],
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


# LOTTEON 핸들러 등록 — 본 파일 내부 상수 사용.
SITE_HANDLERS["LOTTEON"] = SiteHandler(
    site="LOTTEON",
    extract_js=LOTTEON_EXTRACT_JS,
    requires_login=True,
    login_url=LOTTEON_LOGIN_URL,
    home_url=LOTTEON_HOME_URL,
    login_selectors=LOTTEON_LOGIN_SELECTORS,
    pre_extract_wait_ms=5_000,
    pre_extract_marker_js="",
    extract_retry_field="best_benefit_price",
)


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

    오토튠 페이지가 설치 트리거 시 `autotune-daemon-setup_did=…_backend=…exe`
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
        f"{backend_url}/api/v1/samba/sourcing-accounts/extension-key",
        headers={
            "X-Device-Id": device_id,
            "User-Agent": "autotune-daemon/1.0",
            "Origin": "chrome-extension://autotune-daemon",
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
        f"{backend_url}/api/v1/samba/sourcing-accounts/login-credential",
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


async def lotteon_auto_login(page: Page, credential: dict[str, str]) -> bool:
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


async def fetch_credential(
    client: httpx.AsyncClient,
    backend_url: str,
    device_id: str,
    api_key: str,
    site_name: str,
) -> dict[str, str] | None:
    """등록된 site_name 기본 계정의 username/password 평문 조회."""
    r = await client.get(
        f"{backend_url}/api/v1/samba/sourcing-accounts/login-credential",
        params={"site_name": site_name},
        headers={
            "X-Device-Id": device_id,
            "X-Api-Key": api_key,
        },
        timeout=15.0,
    )
    if r.status_code == 404:
        logger.warning(
            "%s 기본 계정 미등록 — 삼바웨이브 화면에서 소싱처 계정 추가 필요",
            site_name,
        )
        return None
    if r.status_code != 200:
        logger.warning(
            "%s login-credential 실패 status=%s body=%s",
            site_name,
            r.status_code,
            r.text[:200],
        )
        return None
    data = r.json() or {}
    if not data.get("username") or not data.get("password"):
        return None
    return {"username": data["username"], "password": data["password"]}


async def is_site_logged_in(page: Page, handler: SiteHandler) -> bool:
    """handler.home_url 방문 + login_check_js 로 로그인 여부 확정.

    LOTTEON 은 login_check_js 미정의 시 본 파일 내부 is_lotteon_logged_in 위임.
    """
    if handler.site == "LOTTEON" and not handler.login_check_js:
        return await is_lotteon_logged_in(page)
    if not handler.home_url:
        return False
    try:
        await page.goto(handler.home_url, wait_until="domcontentloaded", timeout=20_000)
    except Exception as exc:
        logger.warning("%s home 로드 실패: %s", handler.site, exc)
        return False
    await page.wait_for_timeout(3_000)
    if not handler.login_check_js:
        return False
    try:
        result = await page.evaluate(handler.login_check_js)
    except Exception as exc:
        logger.warning("%s login 검사 evaluate 실패: %s", handler.site, exc)
        return False
    return result == "logged_in"


async def auto_login_site(
    page: Page, handler: SiteHandler, credential: dict[str, str]
) -> bool:
    """handler.login_url + login_selectors 로 form fill + submit + 검증."""
    logger.info(
        "%s 자동로그인 시작 (계정=%s)",
        handler.site,
        credential["username"][:4] + "***",
    )
    try:
        await page.goto(
            handler.login_url, wait_until="domcontentloaded", timeout=30_000
        )
    except Exception as exc:
        logger.warning("%s 로그인 페이지 로드 실패: %s", handler.site, exc)
        return False
    await page.wait_for_timeout(2_500)

    selectors_payload = json.dumps(handler.login_selectors)
    cred_payload = json.dumps(credential)
    fill_js = f"""
    (() => {{
      const sel = {selectors_payload}
      const cred = {cred_payload}
      const nativeSetter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype, 'value').set
      function pick(arr) {{
        if (typeof arr === 'string') arr = [arr]
        for (const s of arr) {{
          const el = document.querySelector(s)
          if (el) return el
        }}
        return null
      }}
      const idField = pick(sel.id)
      const pwField = pick(sel.pw)
      if (!idField || !pwField) {{
        return {{ ok: false, reason: 'fields not found' }}
      }}
      idField.focus()
      nativeSetter.call(idField, cred.username)
      idField.dispatchEvent(new Event('input', {{ bubbles: true }}))
      idField.dispatchEvent(new Event('change', {{ bubbles: true }}))
      pwField.focus()
      nativeSetter.call(pwField, cred.password)
      pwField.dispatchEvent(new Event('input', {{ bubbles: true }}))
      pwField.dispatchEvent(new Event('change', {{ bubbles: true }}))
      const btn = pick(sel.btn)
      if (!btn) return {{ ok: false, reason: 'btn not found' }}
      btn.click()
      return {{ ok: true }}
    }})()
    """
    res = await page.evaluate(fill_js)
    if not (isinstance(res, dict) and res.get("ok")):
        logger.warning("%s 로그인 form fill 실패: %s", handler.site, res)
        return False

    # 로그인 후 세션 안정화 대기. 최대 15초.
    for _ in range(15):
        await page.wait_for_timeout(1_000)
        if await is_site_logged_in(page, handler):
            logger.info("%s 자동로그인 성공", handler.site)
            return True
    logger.warning(
        "%s 자동로그인 — 15초 후에도 확정 안 됨 (CAPTCHA 의심)", handler.site
    )
    return False


async def ensure_logged_in_for_site(
    page: Page,
    client: httpx.AsyncClient,
    backend_url: str,
    device_id: str,
    api_key: str,
    handler: SiteHandler,
) -> bool:
    """site 별 로그인 상태 확인 → 미로그인 시 1회 자동로그인 시도."""
    if not handler.requires_login:
        return True
    if await is_site_logged_in(page, handler):
        logger.info("%s 세션 살아있음 — 자동로그인 스킵", handler.site)
        return True
    cred = await fetch_credential(client, backend_url, device_id, api_key, handler.site)
    if not cred:
        logger.error(
            "%s 자격증명 미등록 — 삼바웨이브에서 %s 기본 계정 추가 필요",
            handler.site,
            handler.site,
        )
        return False
    return await auto_login_site(page, handler, cred)


async def ensure_logged_in(
    page: Page,
    client: httpx.AsyncClient,
    backend_url: str,
    device_id: str,
    api_key: str,
) -> bool:
    """하위호환 shim — LOTTEON 로그인 확인."""
    return await ensure_logged_in_for_site(
        page,
        client,
        backend_url,
        device_id,
        api_key,
        SITE_HANDLERS["LOTTEON"],
    )


# ====================================================================
# 잡 폴링 / 처리
# ====================================================================


async def fetch_job(
    client: httpx.AsyncClient,
    backend_url: str,
    device_id: str,
    api_key: str,
    allowed_sites: str = "LOTTEON",
) -> dict[str, Any] | None:
    try:
        r = await client.get(
            f"{backend_url}/api/v1/samba/proxy/sourcing/collect-queue",
            headers={
                "X-Api-Key": api_key,
                "X-Device-Id": device_id,
                "X-Allowed-Sites": allowed_sites,
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
    api_key: str,
) -> bool:
    url = f"{backend_url}/api/v1/samba/proxy/sourcing/collect-result"
    body = {"requestId": request_id, "data": data}
    headers = {"X-Api-Key": api_key}
    for attempt in range(len(_RETRY_DELAYS) + 1):
        try:
            r = await client.post(url, json=body, headers=headers, timeout=15.0)
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


async def extract_pdp(
    page: Page, url: str, product_id: str, handler: SiteHandler
) -> dict[str, Any]:
    """사이트 핸들러 기반 PDP 추출 — marker 폴링 + extract_js + 재시도."""
    await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    await page.evaluate(f"window.__PRD_ID__ = {json.dumps(product_id)}")

    if handler.pre_extract_marker_js:
        deadline = handler.pre_extract_marker_timeout_ms
        step = 500
        elapsed = 0
        while elapsed < deadline:
            try:
                hit = await page.evaluate(handler.pre_extract_marker_js)
            except Exception:
                hit = False
            if hit:
                break
            await page.wait_for_timeout(step)
            elapsed += step
        await page.wait_for_timeout(handler.pre_extract_wait_ms)
    else:
        await page.wait_for_timeout(handler.pre_extract_wait_ms)

    data = await page.evaluate(handler.extract_js)
    retry_field = handler.extract_retry_field
    if (
        retry_field
        and isinstance(data, dict)
        and not data.get(retry_field)
        and (
            (data.get("sale_price") or 0) > 0
            or (data.get("options") and len(data["options"]) > 0)
        )
    ):
        await page.wait_for_timeout(3_000)
        data2 = await page.evaluate(handler.extract_js)
        if isinstance(data2, dict) and data2.get(retry_field):
            data = data2
    return (
        data
        if isinstance(data, dict)
        else {
            "success": False,
            "error": "evaluate 결과 비dict",
        }
    )


async def extract_lotteon_pdp(page: Page, url: str, product_id: str) -> dict[str, Any]:
    """하위호환 shim — LOTTEON 핸들러로 위임."""
    return await extract_pdp(page, url, product_id, SITE_HANDLERS["LOTTEON"])


async def process_job(
    page: Page,
    client: httpx.AsyncClient,
    backend_url: str,
    job: dict[str, Any],
    state: DaemonState,
    api_key: str,
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

    handler = SITE_HANDLERS.get(site)
    if not handler or jtype != "detail":
        logger.warning("범위 밖 잡 (site=%s type=%s) — 실패 회신", site, jtype)
        await post_result(
            client,
            backend_url,
            request_id,
            {
                "success": False,
                "error": f"daemon scope detail only (got {site}/{jtype})",
            },
            api_key,
        )
        state.record_failure()
        return None

    logger.info("처리 시작 site=%s req=%s pid=%s", site, request_id, product_id)
    t0 = time.time()
    try:
        data = await asyncio.wait_for(
            extract_pdp(page, url, product_id, handler), timeout=50.0
        )
    except asyncio.TimeoutError:
        logger.warning("PDP 추출 타임아웃 req=%s pid=%s", request_id, product_id)
        await post_result(
            client,
            backend_url,
            request_id,
            {"success": False, "error": "daemon PDP 추출 타임아웃"},
            api_key,
        )
        state.record_failure()
        return None
    except Exception as exc:
        logger.exception("PDP 추출 예외 req=%s pid=%s: %s", request_id, product_id, exc)
        await post_result(
            client,
            backend_url,
            request_id,
            {"success": False, "error": f"daemon 예외: {exc}"},
            api_key,
        )
        state.record_failure()
        return None

    ok = await post_result(client, backend_url, request_id, data, api_key)
    dt = time.time() - t0
    if ok and data.get("success"):
        bp = data.get("best_benefit_price") or 0
        nopt = len(data.get("options") or [])
        logger.info(
            "완료 req=%s pid=%s 혜택가=%s 옵션=%d (%.1fs)",
            request_id,
            product_id,
            f"{bp:,}",
            nopt,
            dt,
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

    # 활성 사이트 — `--sites=LOTTEON,ABCmart,SSG` 콤마 구분. 기본 LOTTEON 하위호환.
    raw_sites = (getattr(args, "sites", "") or "LOTTEON").strip()
    active_sites = [
        s.strip() for s in raw_sites.split(",") if s.strip() in SITE_HANDLERS
    ]
    if not active_sites:
        active_sites = ["LOTTEON"]
    allowed_sites_header = ",".join(active_sites)
    login_sites = [s for s in active_sites if SITE_HANDLERS[s].requires_login]
    dialog_accept_sites = {
        s for s in active_sites if SITE_HANDLERS[s].dialog_policy == "accept"
    }

    logger.info(
        "데몬 시작 device_id=%s backend=%s profile=%s sites=%s",
        args.device_id,
        backend_url,
        profile_dir,
        allowed_sites_header,
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

        # launch + new_context(storage_state) — persistent_context 는 headless=True 무시하고
        # 창 띄우는 알려진 버그가 있어 쿠키만 storage_state.json 영속 저장.
        storage_state_path = profile_dir / "storage_state.json"
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=args.headless,
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
            )
            context_kwargs: dict[str, Any] = {
                "viewport": {"width": 1280, "height": 900},
                "user_agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
            }
            if storage_state_path.exists():
                context_kwargs["storage_state"] = str(storage_state_path)
            context: BrowserContext = await browser.new_context(**context_kwargs)
            page = await context.new_page()

            # SSG 임직원 alert 자동 dismiss — 띄워두면 페이지 멈춰 다음 잡 차단.
            if dialog_accept_sites:

                async def _on_dialog(dialog):
                    try:
                        logger.info(
                            "dialog auto-accept type=%s msg=%s",
                            dialog.type,
                            (dialog.message or "")[:80],
                        )
                        await dialog.accept()
                    except Exception:
                        try:
                            await dialog.dismiss()
                        except Exception:
                            pass

                page.on("dialog", lambda d: asyncio.create_task(_on_dialog(d)))

            async def _save_storage_state() -> None:
                try:
                    await context.storage_state(path=str(storage_state_path))
                except Exception as exc:
                    logger.debug("storage_state 저장 실패(무시): %s", exc)

            # 시작 시 로그인 — requires_login 사이트 각각.
            for _site in login_sites:
                if not await ensure_logged_in_for_site(
                    page,
                    http_client,
                    backend_url,
                    args.device_id,
                    api_key,
                    SITE_HANDLERS[_site],
                ):
                    logger.error(
                        "%s 초기 로그인 실패 — 종료 (supervisor 재기동 유도)",
                        _site,
                    )
                    await context.close()
                    await browser.close()
                    return 3
            if login_sites:
                await _save_storage_state()

            idle_logged_at = 0.0
            while True:
                if state.should_die():
                    logger.error(
                        "연속 실패 %d 건 초과 — 종료(supervisor 재기동 유도)",
                        state.consecutive_fail,
                    )
                    await context.close()
                    await browser.close()
                    return 1

                # 로그인 만료 누적 시 재로그인 (requires_login 사이트 각각).
                if login_sites and state.consecutive_login_required >= 3:
                    logger.warning("login_required 3회 연속 — 재로그인 시도")
                    _all_ok = True
                    for _site in login_sites:
                        if not await ensure_logged_in_for_site(
                            page,
                            http_client,
                            backend_url,
                            args.device_id,
                            api_key,
                            SITE_HANDLERS[_site],
                        ):
                            logger.error("%s 재로그인 실패", _site)
                            _all_ok = False
                            break
                    if not _all_ok:
                        await context.close()
                        await browser.close()
                        return 4
                    state.reset_login_required()
                    await _save_storage_state()

                job = await fetch_job(
                    http_client,
                    backend_url,
                    args.device_id,
                    api_key,
                    allowed_sites=allowed_sites_header,
                )
                if not job:
                    now = time.time()
                    if now - idle_logged_at > 30:
                        logger.info(
                            "대기 중 (processed=%d ok=%d fail=%d)",
                            state.processed,
                            state.succeeded,
                            state.failed,
                        )
                        idle_logged_at = now
                    await asyncio.sleep(args.poll_interval)
                    continue

                await process_job(page, http_client, backend_url, job, state, api_key)


def _setup_logging() -> None:
    """파일 로깅 (RotatingFileHandler) — --noconsole 빌드에서 stderr 사라져도 로그 영구 보존."""
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    formatter = logging.Formatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    # 기존 핸들러 제거 — basicConfig 중복 방지
    for h in list(root.handlers):
        root.removeHandler(h)
    try:
        log_path = _log_file_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            str(log_path),
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        fh.setFormatter(formatter)
        root.addHandler(fh)
    except Exception:
        pass
    # stderr 핸들러도 추가 — --console 빌드 / .py 실행 시 화면 출력
    try:
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(formatter)
        root.addHandler(sh)
    except Exception:
        pass


# ====================================================================
# 트레이 아이콘 — supervisor 모드에서 daemon thread 로 실행.
# 콘솔창 대체용. 우클릭 메뉴: 로그 열기 / 폴더 열기 / 버전 / 종료.
# ====================================================================


def _open_log_file() -> None:
    try:
        log_path = _log_file_path()
        if not log_path.exists():
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.touch()
        os.startfile(str(log_path))  # type: ignore[attr-defined]
    except Exception as exc:
        logger_print(f"로그 열기 실패: {exc}")


def _open_install_dir() -> None:
    try:
        d = _install_dir()
        d.mkdir(parents=True, exist_ok=True)
        os.startfile(str(d))  # type: ignore[attr-defined]
    except Exception as exc:
        logger_print(f"폴더 열기 실패: {exc}")


def _make_tray_icon_image():
    """64x64 단색 PNG 메모리 이미지 생성 (외부 파일 의존성 X)."""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # 진한 주황 원 + 흰색 S
    d.ellipse((2, 2, 62, 62), fill=(234, 88, 12, 255))
    try:
        d.text((20, 14), "S", fill=(255, 255, 255, 255))
    except Exception:
        pass
    return img


_tray_icon_ref: Any = None


def _start_tray_icon() -> None:
    """tray icon daemon thread 시작. supervisor 가 메인 스레드 점유."""
    global _tray_icon_ref
    try:
        import pystray  # type: ignore
    except Exception as exc:
        logger_print(f"pystray import 실패 — 트레이 스킵: {exc}")
        return

    image = _make_tray_icon_image()

    def _on_quit(icon: Any, _item: Any) -> None:
        try:
            icon.stop()
        except Exception:
            pass
        # supervisor + 현재 worker 종료. os._exit(0) 으로 강제.
        os._exit(0)

    def _on_log(_icon: Any, _item: Any) -> None:
        _open_log_file()

    def _on_folder(_icon: Any, _item: Any) -> None:
        _open_install_dir()

    def _on_version(_icon: Any, _item: Any) -> None:
        try:
            ver_path = _install_dir() / "version.txt"
            ver_path.parent.mkdir(parents=True, exist_ok=True)
            ver_path.write_text(
                f"autotune-daemon v{DAEMON_VERSION}\n", encoding="utf-8"
            )
            os.startfile(str(ver_path))  # type: ignore[attr-defined]
        except Exception:
            pass

    menu = pystray.Menu(
        pystray.MenuItem("로그 열기", _on_log),
        pystray.MenuItem("설치 폴더 열기", _on_folder),
        pystray.MenuItem(f"버전 {DAEMON_VERSION}", _on_version),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("종료", _on_quit),
    )
    icon = pystray.Icon(
        "autotune-daemon",
        image,
        f"오토튠 데몬 v{DAEMON_VERSION}",
        menu,
    )
    _tray_icon_ref = icon

    def _run() -> None:
        try:
            icon.run()
        except Exception as exc:
            logger_print(f"tray run 예외: {exc}")

    t = threading.Thread(target=_run, name="tray-icon", daemon=True)
    t.start()


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="오토튠 헤드리스 데몬 (LOTTEON/ABCmart/SSG)"
    )
    p.add_argument(
        "--sites",
        default=os.environ.get("DAEMON_SITES", "LOTTEON"),
        help=(
            "처리 사이트 콤마구분 (예: LOTTEON,ABCmart,SSG). "
            "X-Allowed-Sites 헤더로 백엔드에 전달. 기본 LOTTEON 하위호환."
        ),
    )
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
            str(Path.home() / ".autotune_daemon" / "chromium_profile"),
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


async def _check_and_self_update(client: httpx.AsyncClient, backend_url: str) -> bool:
    """백엔드 버전 체크 → 신버전이면 True 반환(caller 가 종료 → 재시작 시 신버전 다운로드).

    실패 시 False (현 버전 그대로 진행). 네트워크 일시 장애로 자가 종료되는 사고 방지.
    """
    try:
        r = await client.get(
            f"{backend_url}/api/v1/samba/proxy/autotune-daemon/latest-version",
            timeout=10.0,
        )
        if r.status_code != 200:
            return False
        data = r.json() or {}
        latest = (data.get("version") or "").strip()
        if not latest:
            return False

        def _vt(s: str) -> tuple[int, ...]:
            try:
                return tuple(int(x) for x in s.split(".") if x.isdigit())
            except Exception:
                return ()

        if _vt(latest) <= _vt(DAEMON_VERSION):
            return False  # 로컬 = latest 또는 더 신버전
        logger.info(
            "신버전 감지: 현재=%s latest=%s — 자기 종료 → 다음 시작 시 갱신",
            DAEMON_VERSION,
            latest,
        )
        return True
    except Exception as exc:
        logger.debug("버전 체크 실패(무시): %s", exc)
        return False


def _supervisor_loop() -> int:
    """parent supervisor — 자기 자신을 --worker 모드로 spawn + 죽으면 backoff 재시작.

    NSSM/Service 대체. admin 권한 불필요. UAC 트리거 X.
    parent (supervisor) = 항상 살아있는 가벼운 process.
    child (worker) = 실제 Playwright + polling.

    Backoff: 정상 가동 10초 안에 죽으면 즉시 죽은 것으로 간주 →
    재시작 간격 증가 (5s → 30s → 60s 상한). 정상 가동 ≥10s 시 backoff 리셋.
    """
    logger_print(f"supervisor 시작 pid={os.getpid()}")
    # 트레이 아이콘 시작 — Windows + frozen 일 때만. 콘솔창 없이 상태 표시 + 종료 메뉴 제공.
    if os.name == "nt":
        try:
            _start_tray_icon()
        except Exception as exc:
            logger_print(f"트레이 시작 실패(무시): {exc}")
    restart_count = 0
    backoff = 5
    BACKOFF_MAX = 60
    HEALTHY_SECS = 10
    while True:
        # frozen .exe 모드: sys.executable = daemon.exe, script path 불필요
        # .py 모드: sys.executable = python.exe, sys.argv[0] (script path) 필요
        if _is_frozen():
            cmd = [sys.executable, *sys.argv[1:], "--worker"]
        else:
            cmd = [sys.executable, sys.argv[0], *sys.argv[1:], "--worker"]
        try:
            # CREATE_NEW_PROCESS_GROUP + CREATE_NO_WINDOW — worker 자식이 새 콘솔창 안 띄움
            creationflags = (
                (CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW) if os.name == "nt" else 0
            )
            # worker stdout/stderr 도 log 파일로 redirect — 파편화된 출력 방지
            try:
                log_fp = open(str(_log_file_path()), "a", encoding="utf-8")
            except Exception:
                log_fp = subprocess.DEVNULL  # type: ignore[assignment]
            child = subprocess.Popen(
                cmd,
                creationflags=creationflags,
                stdout=log_fp,
                stderr=log_fp,
                stdin=subprocess.DEVNULL,
            )
        except Exception as exc:
            logger_print(
                f"supervisor: worker spawn 실패: {exc} — {backoff}초 후 재시도"
            )
            time.sleep(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX)
            continue
        start_ts = time.time()
        logger_print(
            f"supervisor: worker spawn pid={child.pid} "
            f"(총 {restart_count + 1}회, backoff={backoff}s)"
        )
        try:
            rc = child.wait()
        except KeyboardInterrupt:
            logger_print("supervisor: KeyboardInterrupt — worker 종료 후 본인도 종료")
            try:
                child.terminate()
            except Exception:
                pass
            return 0
        duration = time.time() - start_ts
        logger_print(f"supervisor: worker exit rc={rc} duration={duration:.1f}s")
        if rc == 0:
            logger_print("supervisor: worker 정상 종료 — supervisor 도 종료")
            return 0
        if duration >= HEALTHY_SECS:
            backoff = 5  # 정상 가동했으니 backoff 리셋
        else:
            backoff = min(backoff * 2, BACKOFF_MAX)
        restart_count += 1
        logger_print(f"supervisor: {backoff}초 후 재시작 (총 {restart_count}회)")
        time.sleep(backoff)


def main() -> int:
    # 1. frozen 모드 + install dir 아닐 때 → self-install + 재시작 후 종료
    if _is_frozen() and not _running_from_install_dir():
        logger_print(f"첫 실행 감지 — {_install_dir()} 로 자기 설치 후 재시작")
        _self_install_and_relaunch()
        # _self_install_and_relaunch 가 os._exit(0) 호출하므로 이 라인 도달 X
        return 0

    # 2. --worker 인자 없으면 supervisor 모드 (자기를 --worker 로 spawn + watchdog)
    if "--worker" not in sys.argv:
        return _supervisor_loop()

    # 3. --worker 인자 있으면 실제 작업 (이 분기에서만 Playwright + polling)
    _setup_logging()
    # argparse 가 unknown arg 무시하도록 — --worker 만 제거 후 parse
    sys.argv = [a for a in sys.argv if a != "--worker"]
    args = _parse_args()
    logger.info(
        "worker v%s 시작 pid=%d (frozen=%s install_dir=%s)",
        DAEMON_VERSION,
        os.getpid(),
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
