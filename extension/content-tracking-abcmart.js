/**
 * content-tracking-abcmart.js
 *
 * ABCmart / GrandStage 주문상세 → div.status-info 의 .info-desc(택배사) +
 * .info-link(송장번호) 추출.
 * URL: https://abcmart.a-rt.com/mypage/order/read-order-detail?orderNo={주문번호}
 *      https://grandstage.a-rt.com/mypage/order/read-order-detail?orderNo={주문번호}
 * 출처: overlink-invoice-extension config.js ABC/ABCGS 셀렉터 검증.
 */
;(() => {
  'use strict'

  function isOrderCancelled() {
    try {
      const text = (document.body?.innerText || '').slice(0, 8000)
      return /(취소완료|취소처리완료|구매취소완료|주문이\s*취소|취소된\s*주문)/.test(text)
    } catch { return false }
  }

  async function waitFor(selector, timeoutMs = 12000) {
    const start = Date.now()
    while (Date.now() - start < timeoutMs) {
      const el = document.querySelector(selector)
      if (el && (el.textContent || '').trim()) return el
      await new Promise(r => setTimeout(r, 300))
    }
    return null
  }

  function isLoginRedirect() {
    try {
      const href = location.href || ''
      if (href.indexOf('/member/login') !== -1) return true
      if (href.indexOf('login') !== -1 && href.indexOf('order-detail') === -1) return true
      if (href && href.indexOf('order-detail') === -1) return true
      return false
    } catch { return false }
  }

  async function scrape() {
    if (isLoginRedirect()) {
      return { success: false, needsLogin: true, error: 'needs_login: ABCmart 로그인 페이지로 리다이렉트' }
    }
    if (isOrderCancelled()) {
      return { success: false, cancelled: true, error: 'order_cancelled' }
    }
    const courierEl = await waitFor('div.status-info .info-desc', 10000)
    if (!courierEl) {
      if (isLoginRedirect()) {
        return { success: false, needsLogin: true, error: 'needs_login: ABCmart 로그인 페이지로 리다이렉트' }
      }
      return { success: false, error: 'no_tracking: status-info 미로드 (미발송)' }
    }
    const courierName = courierEl.textContent.trim()
    const trackingEl = document.querySelector('div.status-info .info-link')
    const raw = (trackingEl?.textContent || '').trim()
    const m = raw.match(/\d{8,}/)
    const trackingNumber = m ? m[0] : raw
    if (!trackingNumber) {
      return { success: false, error: 'no_tracking: 송장번호 미표시', courierName }
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
