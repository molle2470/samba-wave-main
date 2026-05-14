/**
 * content-tracking-lotteon.js
 *
 * 롯데ON 주문상세 → "배송상세조회" 버튼 클릭 → dialog 내 택배사/송장번호 추출.
 * URL: https://www.lotteon.com/p/order/claim/orderDetail?odNo={주문번호}
 * 출처: overlink-invoice-extension content-lotteon.js 셀렉터 검증.
 */
;(() => {
  'use strict'

  function isOrderCancelled() {
    try {
      const text = (document.body?.innerText || '').slice(0, 8000)
      return /(취소완료|취소처리완료|구매취소완료|주문이\s*취소|취소된\s*주문)/.test(text)
    } catch { return false }
  }

  function clickDeliveryDetail() {
    for (const btn of document.querySelectorAll('button')) {
      const t = btn.textContent.trim()
      if (t === '배송상세조회' || t === '배송 상세 조회' || t === '배송상세 조회') {
        btn.click()
        return true
      }
    }
    for (const a of document.querySelectorAll('a')) {
      if (a.textContent.trim().includes('배송상세조회')) { a.click(); return true }
    }
    return false
  }

  function waitForDialog(timeout = 5000) {
    return new Promise((resolve, reject) => {
      const existing = document.querySelector('dialog[open], [role="dialog"]')
      if (existing) return resolve(existing)
      const start = Date.now()
      const obs = new MutationObserver(() => {
        const d = document.querySelector('dialog[open], [role="dialog"]')
        if (d) { obs.disconnect(); resolve(d) }
        else if (Date.now() - start > timeout) { obs.disconnect(); reject(new Error('dialog timeout')) }
      })
      obs.observe(document.body, { childList: true, subtree: true, attributes: true })
      setTimeout(() => {
        obs.disconnect()
        const d = document.querySelector('dialog[open], [role="dialog"]')
        d ? resolve(d) : reject(new Error('dialog timeout'))
      }, timeout)
    })
  }

  function extractField(dialog, label) {
    for (const el of dialog.querySelectorAll('*')) {
      if ((el.textContent || '').trim() === label && el.children.length === 0) {
        const sib = el.nextElementSibling
        if (!sib) continue
        const link = sib.querySelector('a') || (sib.tagName === 'A' ? sib : null)
        return (link?.textContent || sib.textContent || '').trim()
      }
    }
    return ''
  }

  function isLoginRedirect() {
    try {
      const href = location.href || ''
      if (href.indexOf('member.lotteon.com') !== -1) return true
      if (href.indexOf('/member/login') !== -1) return true
      if (href && href.indexOf('orderDetail') === -1 && href.indexOf('order/claim') === -1) return true
      return false
    } catch { return false }
  }

  async function scrape() {
    if (isLoginRedirect()) {
      return { success: false, needsLogin: true, error: 'needs_login: LOTTEON 로그인 페이지로 리다이렉트' }
    }
    if (isOrderCancelled()) {
      return { success: false, cancelled: true, error: 'order_cancelled' }
    }
    let dialog = document.querySelector('dialog[open], [role="dialog"]')
    if (!dialog) {
      if (!clickDeliveryDetail()) {
        if (isLoginRedirect()) {
          return { success: false, needsLogin: true, error: 'needs_login: LOTTEON 로그인 페이지로 리다이렉트' }
        }
        return { success: false, error: 'no_tracking: 배송상세조회 버튼 없음 (미발송)' }
      }
      try { dialog = await waitForDialog(5000) } catch {
        return { success: false, error: 'dialog 미열림' }
      }
    }
    await new Promise(r => setTimeout(r, 1000))

    let courierName = extractField(dialog, '택배사')
    let trackingNumber = extractField(dialog, '송장번호')

    if (!trackingNumber) {
      for (const link of dialog.querySelectorAll('a[href*="tracking"], a[href*="InvNo"]')) {
        const t = link.textContent.trim()
        if (/^\d{8,}$/.test(t)) { trackingNumber = t; break }
      }
    }
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
