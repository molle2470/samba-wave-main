/**
 * content-tracking-oliveyoung.js
 *
 * 올리브영 주문상세에서 "배송 정보" 영역의 li 안 <em>택배사</em>/<em>운송장번호</em> 다음
 * 텍스트 노드를 추출.
 * URL: https://www.oliveyoung.co.kr/store/mypage/getOrderDetail.do?ordNo={주문번호}
 * 출처: overlink-invoice-extension config.js OLIVEYOUNG 셀렉터 검증.
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
      if (el) return el
      await new Promise(r => setTimeout(r, 300))
    }
    return null
  }

  function extractByLabel(label) {
    // <em>택배사</em>텍스트노드 형태에서 텍스트 노드만 추출
    const ems = document.querySelectorAll('em')
    for (const em of ems) {
      if ((em.textContent || '').trim() === label) {
        let next = em.nextSibling
        while (next) {
          if (next.nodeType === 3) {
            const t = next.textContent.trim().replace(/^[:：\s]+/, '')
            if (t) return t
          } else if (next.nodeType === 1) {
            const t = (next.textContent || '').trim()
            if (t) return t
          }
          next = next.nextSibling
        }
      }
    }
    return ''
  }

  async function scrape() {
    if (isOrderCancelled()) {
      return { success: false, cancelled: true, error: 'order_cancelled' }
    }
    await waitFor('.lineBox2, h3', 10000)
    await new Promise(r => setTimeout(r, 800))

    const courierName = extractByLabel('택배사')
    const trackingRaw = extractByLabel('운송장번호') || extractByLabel('송장번호')
    const m = (trackingRaw || '').match(/\d{8,}/)
    const trackingNumber = m ? m[0] : ''
    if (!trackingNumber) {
      return { success: false, error: 'no_tracking: 운송장번호 미표시 (미발송)', courierName }
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
