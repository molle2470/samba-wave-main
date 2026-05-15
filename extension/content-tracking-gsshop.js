/**
 * content-tracking-gsshop.js
 *
 * GS샵 배송조회 팝업: a[data-action="dlvTrace"] data-* 속성에서 택배사 코드/송장번호 추출.
 * URL: https://www.gsshop.com/ord/dlvcursta/popup/ordDtl.gs?ordNo={주문번호}&ecOrdTypCd=S
 * 출처: overlink-invoice-extension config.js GS courierCodeMap 검증.
 */
;(() => {
  'use strict'

  // GS샵 택배사 코드 → 한글명 매핑 (overlink config.js 이식)
  const COURIER_CODE_MAP = {
    CJ: 'CJ대한통운', HJ: '한진택배', KG: '로젠택배',
    LO: '롯데택배', LT: '롯데택배', EP: '우체국택배', POST: '우체국택배',
    RZ: '로젠택배', DS: '대신택배', IL: '일양로지스', KD: '경동택배',
    CH: '천일택배', HD: '롯데택배', SL: 'SLX택배',
    CR: 'CVSnet편의점택배', DH: 'DHL', GS: 'GSMNtoN',
  }

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
      if (el) return el
      await new Promise(r => setTimeout(r, 300))
    }
    return null
  }

  async function scrape() {
    if (isOrderCancelled()) {
      return { success: false, cancelled: true, error: 'order_cancelled' }
    }
    const a = await waitFor('a[data-action="dlvTrace"]', 12000)
    if (!a) {
      return { success: false, error: 'no_tracking: dlvTrace 링크 없음 (미발송)' }
    }
    const code = (a.getAttribute('data-dlvs-co-cd') || '').toUpperCase()
    const trackingNumber = (a.getAttribute('data-inv-no') || '').trim()
    const courierName = COURIER_CODE_MAP[code] || code || ''
    if (!trackingNumber) {
      return { success: false, error: 'no_tracking: data-inv-no 비어있음', courierName }
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
