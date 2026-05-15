/**
 * content-tracking-fashionplus.js
 *
 * 패션플러스 주문상세에서 송장 추출. 패션플러스는 goodsflow trace를 별도 사용하지만
 * 주문상세 페이지의 배송조회 영역에 택배사/송장이 노출되는 경우도 있어
 * 우선 DOM을 살피고 없으면 trace 링크의 itemNo로 보조.
 * URL: https://www.fashionplus.co.kr/mypage/order/detail/{주문번호}
 */
;(() => {
  'use strict'

  function isOrderCancelled() {
    try {
      const text = (document.body?.innerText || '').slice(0, 8000)
      return /(취소완료|취소처리완료|구매취소완료|주문이\s*취소|취소된\s*주문)/.test(text)
    } catch { return false }
  }

  async function waitFor(selector, timeoutMs = 8000) {
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
    // 패션플러스: "배송조회" 링크 또는 텍스트 영역에서 택배사+송장 추출
    // DOM 구조가 일정치 않아 휴리스틱 — 텍스트에서 정규식
    await waitFor('body', 5000)
    await new Promise(r => setTimeout(r, 1500))

    const text = document.body?.innerText || ''
    // "택배사 : XXX" "송장번호 : 1234567"
    const courierMatch = text.match(/택배사\s*[:：]?\s*([가-힣A-Za-z0-9()]+택배|[가-힣A-Za-z0-9()]+로지스|CJ대한통운|한진|롯데|우체국|로젠)/)
    const trackingMatch = text.match(/송장(?:번호)?\s*[:：]?\s*(\d{8,})/)
    const courierName = courierMatch?.[1]?.trim() || ''
    const trackingNumber = trackingMatch?.[1]?.trim() || ''

    if (!trackingNumber) {
      // goodsflow 링크 형태에서 itemNo 추출 후 보조 페치 (탭 미오픈 단순화 — 여기선 미발송 처리)
      return { success: false, error: 'no_tracking: 패션플러스 송장 미표시 (미발송 가능)' }
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
