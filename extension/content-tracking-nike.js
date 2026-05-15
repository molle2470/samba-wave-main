/**
 * content-tracking-nike.js
 *
 * 나이키 주문상세 페이지의 "배송조회" 외부 링크(href)에서 택배사 도메인 + 송장번호 파라미터 추출.
 * URL: https://www.nike.com/kr/orders/sales/{주문번호}/
 * 출처: overlink-invoice-extension config.js NIKE courierUrlMap/trackingParamMap 이식.
 */
;(() => {
  'use strict'

  const COURIER_URL_MAP = {
    'cjlogistics.com': 'CJ대한통운',
    'hanjin.co.kr': '한진택배',
    'lotteglogis.com': '롯데택배',
    'epost.go.kr': '우체국택배',
    'ilogen.com': '로젠택배',
    'doortodoor.co.kr': 'KGB택배',
    'kdexp.com': '경동택배',
    'cvsnet.co.kr': 'CVSnet편의점택배',
    'daesinlogistics.co.kr': '대신택배',
    'ilyanglogis.com': '일양로지스',
  }
  const TRACKING_PARAM_MAP = {
    'cjlogistics.com': 'gnbInvcNo',
    'hanjin.co.kr': 'waybillNo',
    'lotteglogis.com': 'InvNo',
    'epost.go.kr': 'sid1',
    'ilogen.com': 'slipno',
  }

  function isOrderCancelled() {
    try {
      const text = (document.body?.innerText || '').slice(0, 8000)
      return /(취소완료|취소처리완료|구매취소완료|주문이\s*취소|취소된\s*주문|Cancell?ed)/i.test(text)
    } catch { return false }
  }

  async function waitFor(selector, timeoutMs = 12000) {
    const start = Date.now()
    while (Date.now() - start < timeoutMs) {
      const els = document.querySelectorAll(selector)
      for (const el of els) {
        const href = el.getAttribute('href') || ''
        for (const domain of Object.keys(COURIER_URL_MAP)) {
          if (href.includes(domain)) return el
        }
      }
      await new Promise(r => setTimeout(r, 300))
    }
    return null
  }

  async function scrape() {
    if (isOrderCancelled()) {
      return { success: false, cancelled: true, error: 'order_cancelled' }
    }
    const link = await waitFor('a[href]', 12000)
    if (!link) {
      return { success: false, error: 'no_tracking: 배송조회 링크 없음 (미발송)' }
    }
    const href = link.getAttribute('href') || ''
    let courierName = ''
    let domain = ''
    for (const d of Object.keys(COURIER_URL_MAP)) {
      if (href.includes(d)) { domain = d; courierName = COURIER_URL_MAP[d]; break }
    }
    let trackingNumber = ''
    try {
      const u = new URL(href, location.href)
      const paramName = TRACKING_PARAM_MAP[domain] || ''
      if (paramName) trackingNumber = u.searchParams.get(paramName) || ''
      if (!trackingNumber) {
        for (const v of u.searchParams.values()) {
          if (/^\d{8,}$/.test(v)) { trackingNumber = v; break }
        }
      }
    } catch {}
    if (!trackingNumber) {
      return { success: false, error: 'no_tracking: 송장 파라미터 추출 실패', courierName }
    }
    return { success: true, courierName, trackingNumber }
  }

  function send(requestId, payload) {
    try { chrome.runtime.sendMessage({ type: 'TRACKING_RESULT', requestId, ...payload }) } catch {}
  }

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg?.type === 'TRACKING_REQUEST') {
      sendResponse({ ack: true })
      scrape().then(r => send(msg.requestId, r))
        .catch(err => send(msg.requestId, { success: false, error: String(err?.message || err) }))
      return true
    }
    return false
  })
})()
