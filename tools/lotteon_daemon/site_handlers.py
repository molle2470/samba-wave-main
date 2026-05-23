"""사이트별 PDP 추출 + 로그인 핸들러 레지스트리.

`daemon.py` 가 process_job 에서 site 별로 분기하기 위한 dispatch 테이블.

지원 사이트:
- LOTTEON: 로그인 필수, DOM 추출
- ABCmart/GrandStage: 로그인 시 best_benefit_price 정확, in-tab fetch + DOM 폴백
- SSG: 로그인 불필요, 임직원 alert 자동 dismiss

각 사이트 EXTRACT_JS 는 `backend/domain/samba/plugins/sourcing/<site>.py` 가 dom_ext
에서 읽는 필드 스키마와 일치해야 한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SiteHandler:
    site: str
    extract_js: str
    requires_login: bool = False
    login_url: str = ""
    home_url: str = ""
    login_selectors: dict[str, Any] = field(default_factory=dict)
    login_check_js: str = ""
    dialog_policy: str | None = None  # 'accept' / 'dismiss' / None
    pre_extract_wait_ms: int = 5_000
    pre_extract_marker_js: str = ""
    pre_extract_marker_timeout_ms: int = 15_000
    extract_retry_field: str = "best_benefit_price"


# ─────────────────────────────────────────────────────────────────────────────
# ABCmart / GrandStage
# ─────────────────────────────────────────────────────────────────────────────

ABCMART_LOGIN_URL = "https://abcmart.a-rt.com/login"
ABCMART_HOME_URL = "https://abcmart.a-rt.com"
ABCMART_LOGIN_SELECTORS = {
    "id": ["#loginId", 'input[name="loginId"]', 'input[name="userId"]'],
    "pw": ["#loginPw", 'input[name="loginPw"]', 'input[type="password"]'],
    "btn": ['button[type="submit"]', ".btn-login", "#btnLogin"],
}

# ABCmart 로그인 체크 JS — 헤더 텍스트 기반.
ABCMART_LOGIN_CHECK_JS = r"""
(() => {
  try {
    const txt = (document.body?.innerText || '').substring(0, 600)
    if (/로그아웃|MY\s*PAGE|마이페이지|마이 ABC/.test(txt)) return 'logged_in'
    if (/로그인|회원가입/.test(txt)) return 'logged_out'
    return 'unknown'
  } catch (_) { return 'unknown' }
})()
"""

_ABCMART_MARKER_JS = r"""
(() => {
  try {
    const t = (document.body && document.body.innerText) || ''
    return /최대\s*혜택가\s*[\d,]+\s*원/.test(t)
  } catch (_) { return false }
})()
"""

_ABCMART_EXTRACT_JS = r"""
(async () => {
  const _prdt = window.__PRD_ID__ || ''
  const _result = {
    success: false,
    site_product_id: _prdt,
    name: '',
    original_price: 0,
    sale_price: 0,
    best_benefit_price: 0,
    source_site: 'ABCmart',
    options: [],
    images: [],
    login_required: false,
    _domLoginSignal: 'ambiguous',
  }
  try {
    let apiData = null
    try {
      const resp = await fetch(`/product/info?prdtNo=${_prdt}`, {
        credentials: 'include',
        headers: {
          'Accept': 'application/json, text/plain, */*',
          'X-Requested-With': 'XMLHttpRequest',
        }
      })
      const text = await resp.text()
      try { apiData = JSON.parse(text) } catch (_) {}
    } catch (_) {}

    if (apiData && apiData.prdtName) {
      const pi = apiData.productPrice || {}
      const displayPrice = parseInt(apiData.displayProductPrice || 0)
      const sellAmt = parseInt(pi.sellAmt || 0)
      const normalAmt = parseInt(pi.normalAmt || 0)
      const alwaysDscntAmt = parseInt(apiData.alwaysDscntAmt || 0)
      const loginYn = (apiData.loginYn || '').toUpperCase()
      const coupons = apiData.maxBenefitCoupon || apiData.coupon || []
      const salePrice = displayPrice > 0 ? displayPrice : sellAmt
      let couponDiscount = 0
      for (const c of coupons) couponDiscount += parseInt(c.dscntAmt || 0)
      let benefit = salePrice - alwaysDscntAmt - couponDiscount
      if (benefit <= 0 || benefit > salePrice) benefit = salePrice
      _result.name = (apiData.prdtName || '').trim()
      _result.sale_price = salePrice
      _result.original_price = normalAmt || salePrice
      _result.best_benefit_price = loginYn === 'Y' ? benefit : 0
      _result._domLoginSignal = loginYn === 'Y' ? 'logout_link' : 'login_link'
      _result.login_required = loginYn !== 'Y'
      if (salePrice > 0) _result.success = true
    }

    try {
      const optEls = document.querySelectorAll(
        '[data-prdt-option], .option-list li, .product-option li'
      )
      optEls.forEach((el) => {
        const nm = (el.textContent || '').trim().replace(/\s+/g, ' ')
        if (!nm) return
        const soldOut = /품절|sold\s*out/i.test(nm) || el.classList.contains('disabled')
        _result.options.push({ name: nm.slice(0, 60), stock: soldOut ? 0 : null, isSoldOut: soldOut })
      })
    } catch (_) {}

    if (!_result.best_benefit_price) {
      const bodyText = document.body?.innerText || ''
      const m = bodyText.match(/최대\s*혜택가\s*([\d,]+)\s*원/)
      if (m) {
        const v = parseInt(m[1].replace(/,/g, ''), 10)
        if (v > 0) _result.best_benefit_price = v
      }
    }

    try {
      const imgs = document.querySelectorAll('.product-detail-images img, .swiper-slide img, .thumb img')
      imgs.forEach((img) => {
        let src = img.src || img.getAttribute('data-src') || ''
        if (src.startsWith('//')) src = 'https:' + src
        if (src && !_result.images.includes(src) && _result.images.length < 9) _result.images.push(src)
      })
    } catch (_) {}

    return _result
  } catch (e) {
    return { ..._result, success: false, error: String(e) }
  }
})()
"""


# ─────────────────────────────────────────────────────────────────────────────
# SSG
# ─────────────────────────────────────────────────────────────────────────────

_SSG_MARKER_JS = r"""
(() => {
  try {
    const _src = document.documentElement ? document.documentElement.outerHTML : ''
    if (_src.indexOf('임직원 및 사업자 회원') !== -1 || _src.indexOf('임직원만 구매') !== -1) {
      window.__SSG_STAFF_ONLY__ = true
      return true
    }
    const _href = location.href || ''
    if (_href.indexOf('member.ssg.com/member/login') !== -1) {
      window.__SSG_STAFF_ONLY__ = true; return true
    }
    if (document.title === 'flagMsg') { window.__SSG_STAFF_ONLY__ = true; return true }
    if (_href && _href.indexOf('itemView.ssg') === -1) { window.__SSG_STAFF_ONLY__ = true; return true }
    const hasObj = !!(window.resultItemObj && window.resultItemObj.itemNm)
    if (!hasObj) return false
    return true
  } catch (_) { return false }
})()
"""

# SSG 추출 — domCardPrice > domSalePrice > resultItemObj. sellprc = 정상가 (영구 금지: salePrice 절대 X).
_SSG_EXTRACT_JS = r"""
(() => {
  try {
    if (window.__SSG_STAFF_ONLY__) {
      return { success: false, staffOnly: true, source_site: 'SSG' }
    }
    const obj = window.resultItemObj || {}
    const _intVal = (v) => parseInt((v || '0').toString().replace(/[^0-9]/g, ''), 10) || 0

    let domCardPrice = 0
    let domSalePrice = 0
    try {
      const cardEl = document.querySelector('.cdtl_price.point .ssg_price, .cdtl_card_price .ssg_price')
      if (cardEl) domCardPrice = _intVal(cardEl.textContent)
      if (!domCardPrice) {
        document.querySelectorAll('dt').forEach((dt) => {
          if (dt.textContent.trim() !== '카드혜택가') return
          const dd = dt.nextElementSibling
          if (dd) {
            const em = dd.querySelector('em.ssg_price') || dd
            const v = _intVal(em.textContent)
            if (v) domCardPrice = v
          }
        })
      }
      const saleEl = document.querySelector('.cdtl_new_price.notranslate em.ssg_price, .cdtl_price .ssg_price')
      if (saleEl) domSalePrice = _intVal(saleEl.textContent)
    } catch (_) {}

    const sellprc = _intVal(obj.sellprc)
    const bestAmt = _intVal(obj.bestAmt)
    const norprc = _intVal(obj.norprc || obj.orgPrc)
    const salePrice = domSalePrice || bestAmt
    const originalPrice = norprc || sellprc || salePrice
    const cost = domCardPrice || bestAmt || salePrice

    const options = []
    try {
      const optEls = document.querySelectorAll('.cdtl_opt_list li, [class*="option"] li')
      optEls.forEach((el) => {
        const nm = (el.textContent || '').trim().replace(/\s+/g, ' ')
        if (!nm) return
        const soldOut = /품절/.test(nm) || el.classList.contains('disabled')
        options.push({ name: nm.slice(0, 60), stock: soldOut ? 0 : null, isSoldOut: soldOut })
      })
    } catch (_) {}

    const images = []
    try {
      document.querySelectorAll('.cdtl_imgview img, .swiper-slide img, [class*="thumb"] img').forEach((img) => {
        let src = img.src || img.getAttribute('data-src') || ''
        if (src.startsWith('//')) src = 'https:' + src
        if (src && !images.includes(src) && images.length < 9) images.push(src)
      })
    } catch (_) {}

    return {
      success: salePrice > 0 || cost > 0,
      site_product_id: window.__PRD_ID__ || '',
      name: (obj.itemNm || document.querySelector('meta[property="og:title"]')?.content || '').trim(),
      original_price: originalPrice,
      sale_price: salePrice,
      best_benefit_price: cost,
      domCardPrice,
      domSalePrice,
      images,
      options,
      source_site: 'SSG',
    }
  } catch (e) {
    return { success: false, error: String(e), source_site: 'SSG' }
  }
})()
"""


SITE_HANDLERS: dict[str, SiteHandler] = {
    "ABCmart": SiteHandler(
        site="ABCmart",
        extract_js=_ABCMART_EXTRACT_JS,
        requires_login=True,
        login_url=ABCMART_LOGIN_URL,
        home_url=ABCMART_HOME_URL,
        login_selectors=ABCMART_LOGIN_SELECTORS,
        login_check_js=ABCMART_LOGIN_CHECK_JS,
        pre_extract_marker_js=_ABCMART_MARKER_JS,
        pre_extract_marker_timeout_ms=10_000,
        pre_extract_wait_ms=1_000,
    ),
    "GrandStage": SiteHandler(
        site="GrandStage",
        extract_js=_ABCMART_EXTRACT_JS,  # 동일 도메인 a-rt.com
        requires_login=True,
        login_url=ABCMART_LOGIN_URL,
        home_url=ABCMART_HOME_URL,
        login_selectors=ABCMART_LOGIN_SELECTORS,
        login_check_js=ABCMART_LOGIN_CHECK_JS,
        pre_extract_marker_js=_ABCMART_MARKER_JS,
        pre_extract_marker_timeout_ms=10_000,
        pre_extract_wait_ms=1_000,
    ),
    "SSG": SiteHandler(
        site="SSG",
        extract_js=_SSG_EXTRACT_JS,
        requires_login=False,
        dialog_policy="accept",
        pre_extract_marker_js=_SSG_MARKER_JS,
        pre_extract_marker_timeout_ms=15_000,
        pre_extract_wait_ms=1_000,
    ),
}
