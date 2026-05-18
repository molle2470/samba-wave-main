// content-review-abcmart.js — ABC마트 리뷰 자동작성 (kream-auto-review content-abcmart.js 포팅)
// 인페이지 모달 — "후기작성" 버튼 → 모달 → 별점/라디오/텍스트 → 등록 → 확인 alert
;(() => {
  if (window.__sambaAbcmartReviewListener) {
    try { chrome.runtime.onMessage.removeListener(window.__sambaAbcmartReviewListener) } catch {}
  }

  const TEXTS = [
    '편하게 잘 신고 다니고 있습니다 만족해요',
    '가볍고 착화감이 좋아서 매일 신게 되네요',
    '디자인도 예쁘고 실물이 더 좋습니다 추천해요',
    '사이즈 딱 맞고 품질도 좋아서 재구매 의사 있어요',
    '배송 빠르고 상품 상태도 깔끔하게 잘 왔어요',
    '가성비 최고입니다 이 가격에 이 퀄리티면 만족이에요',
    '색상이 화면과 동일하고 마감도 깔끔합니다 좋아요',
    '생각보다 가볍고 쿠셔닝이 좋아서 오래 걸어도 편해요',
    '선물용으로 샀는데 받는 분도 아주 만족하셨어요',
    '두번째 구매인데 역시 품질이 일정하고 좋습니다',
  ]
  let starToggle = 4
  function randomText() { return TEXTS[Math.floor(Math.random() * TEXTS.length)] }
  function sleep(ms) { return new Promise(r => setTimeout(r, ms)) }
  function rand(a, b) { return Math.floor(Math.random() * (b - a + 1)) + a }

  function getReviewableItems() {
    return Array.from(document.querySelectorAll('button')).filter(b => {
      if (b.textContent.trim() !== '후기작성') return false
      const block = b.closest('tbody') || b.parentElement?.parentElement?.parentElement
      if (!block || block.dataset.sambaReviewed) return false
      return block.textContent.includes('구매확정')
    })
  }

  async function setStarRating(dialog, value) {
    const inputName = 'evltScore1'
    const inp = dialog.querySelector(`input[name="${inputName}"]`)
    if (!inp) return false
    const script = document.createElement('script')
    script.textContent = `
      (function(){
        try {
          var jq = (typeof jQuery !== 'undefined') ? jQuery : $;
          jq('input[name="${inputName}"]').rating('update', ${value});
        } catch(e) {
          var inp = document.querySelector('input[name="${inputName}"]');
          if (inp) { inp.value = ${value}; inp.dispatchEvent(new Event('change', {bubbles:true})); }
        }
      })();
    `
    document.documentElement.appendChild(script)
    script.remove()
    await sleep(700)
    if (String(inp.value) !== String(value)) {
      const c = dialog.querySelector('.rating-container') || inp.closest('.rating-container')
      if (c) {
        const fs = c.querySelector('.filled-stars')
        if (fs) fs.style.width = (value / 5 * 100) + '%'
      }
      inp.value = value
      inp.dispatchEvent(new Event('change', { bubbles: true }))
    }
    return true
  }

  const BLOCKER_KW = ['사이즈 관리', '사이즈관리', '나의 사이즈', '포인트 안내', '개인정보', 'G-log', '혜택 안내']

  function findCloseBtn(c) {
    const x = c.querySelector('.ui-dialog-close, .btn-close, [class*="close"]:not([class*="modal"])')
    if (x && x.offsetHeight > 0) return x
    return Array.from(c.querySelectorAll('a, button')).find(b => {
      const t = b.textContent.trim()
      return ['확인', '닫기', '취소', 'Close', 'X', '×'].includes(t)
    }) || null
  }

  async function dismissBlockers(except, forceAll = false) {
    let dismissed = false
    for (const d of document.querySelectorAll('.ui-dialog-container')) {
      if (d === except) continue
      if (d.offsetHeight === 0) continue
      if (d.querySelector('input[name="evltScore1"]') || d.querySelector('textarea[name="rvwContText"]')) continue
      const text = d.textContent || ''
      const isBlocker = BLOCKER_KW.some(k => text.includes(k))
      if (!isBlocker && !forceAll) continue
      const cb = findCloseBtn(d)
      if (cb) { cb.click(); dismissed = true; await sleep(700) }
      else { d.style.display = 'none'; dismissed = true }
    }
    if (dismissed) {
      await sleep(300)
      for (const ov of document.querySelectorAll('.ui-dialog-overlay, .ui-widget-overlay, .modal-backdrop')) {
        if (ov.offsetHeight > 0) ov.style.display = 'none'
      }
    }
    return dismissed
  }

  async function closeAllModals() {
    for (const d of document.querySelectorAll('.ui-dialog-container')) {
      if (d.offsetHeight === 0) continue
      const action = Array.from(d.querySelectorAll('a, button')).find(b => ['확인', '닫기', 'OK', 'Close'].includes(b.textContent.trim()))
      if (action) { action.click(); await sleep(400) }
      if (d.offsetHeight > 0) {
        const c = d.querySelector('.ui-dialog-close, .btn-close, [class*="close"]')
        if (c) { c.click(); await sleep(400) }
      }
      if (d.offsetHeight > 0) {
        d.style.display = 'none'
        const ov = document.querySelector('.ui-dialog-overlay, .ui-widget-overlay')
        if (ov) ov.style.display = 'none'
      }
    }
  }

  function waitForReviewOrBlocker(timeout = 6000) {
    return new Promise(resolve => {
      let done = false
      const finish = v => { if (!done) { done = true; ob.disconnect(); clearInterval(p); resolve(v) } }
      const check = () => {
        for (const c of document.querySelectorAll('.ui-dialog-container')) {
          if (c.offsetHeight === 0) continue
          if (c.querySelector('input[name="evltScore1"]') || c.querySelector('textarea[name="rvwContText"]')) return { t: 'review', el: c }
          if (BLOCKER_KW.some(k => (c.textContent || '').includes(k))) return { t: 'blocker', el: c }
        }
        return null
      }
      const ex = check()
      if (ex) { resolve(ex.t === 'review' ? ex.el : 'blocker'); return }
      const ob = new MutationObserver(() => { const r = check(); if (r) finish(r.t === 'review' ? r.el : 'blocker') })
      ob.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['style', 'class'] })
      const p = setInterval(() => { const r = check(); if (r) finish(r.t === 'review' ? r.el : 'blocker') }, 500)
      setTimeout(() => finish(null), timeout)
    })
  }

  async function processOne() {
    const items = getReviewableItems()
    if (items.length === 0) return { noItems: true }
    const btn = items[0]
    await dismissBlockers()
    await sleep(500)
    btn.scrollIntoView({ behavior: 'instant', block: 'center' })
    await sleep(rand(700, 1300))
    btn.click()

    let dialog = null
    for (let att = 0; att < 4; att++) {
      dialog = await waitForReviewOrBlocker(att === 0 ? 6000 : 4000)
      if (dialog === 'blocker') {
        await dismissBlockers(null, true)
        await sleep(800)
        btn.click()
        await sleep(800)
        dialog = null
        continue
      }
      if (dialog && dialog !== 'blocker') break
      const blocked = await dismissBlockers(null, true)
      if (blocked) {
        await sleep(800); btn.click(); await sleep(800)
      } else break
    }

    if (!dialog || dialog === 'blocker') {
      await closeAllModals()
      btn.closest('tbody')?.setAttribute('data-samba-reviewed', 'true')
      return { success: false, error: '리뷰 모달 안 열림' }
    }

    await dismissBlockers(dialog, true)
    await sleep(rand(1000, 1500))

    const star = starToggle
    starToggle = starToggle === 4 ? 5 : 4
    const ok = await setStarRating(dialog, star)
    if (!ok) {
      btn.closest('tbody')?.setAttribute('data-samba-reviewed', 'true')
      return { success: false, error: '별점 설정 실패' }
    }
    await sleep(rand(500, 900))

    for (const name of ['evltScore2', 'evltScore3', 'evltScore4', 'evltScore5', 'evltScore6']) {
      const r = dialog.querySelector(`input[name="${name}"][value="2"]`)
      if (r) { r.click(); await sleep(rand(250, 600)) }
    }
    await sleep(rand(400, 800))

    const ta = dialog.querySelector('textarea[name="rvwContText"], textarea#rvw-cont-text, textarea')
    if (!ta) {
      btn.closest('tbody')?.setAttribute('data-samba-reviewed', 'true')
      return { success: false, error: 'textarea 없음' }
    }
    ta.focus(); await sleep(300)
    ta.value = randomText()
    ta.dispatchEvent(new Event('input', { bubbles: true }))
    ta.dispatchEvent(new Event('change', { bubbles: true }))
    await sleep(rand(700, 1200))

    const reg = dialog.querySelector('#save-review')
      || Array.from(dialog.querySelectorAll('a, button')).find(a => a.textContent.trim() === '등록'
        && !a.className.includes('light-gray') && !a.className.includes('btn-dialog') && a.id !== 'reviewMySizeSetBtn')
    if (!reg) {
      btn.closest('tbody')?.setAttribute('data-samba-reviewed', 'true')
      return { success: false, error: '등록 버튼 없음' }
    }
    reg.click()
    await sleep(2000)

    // alert 자동 닫기 (ABC마트는 confirm/alert 사용 — extension은 페이지 컨텍스트 오버라이드 불가하므로
    //  alert는 사용자/브라우저가 자동 처리하거나 chrome.alert blocker로 차단)
    await sleep(800)
    await closeAllModals()
    await sleep(1200)
    await closeAllModals()

    const block = btn.closest('tbody') || btn.parentElement?.parentElement?.parentElement
    if (block) block.dataset.sambaReviewed = 'true'
    return { success: true }
  }

  async function loadMore() {
    // ABC마트는 페이지네이션 — 다음 페이지 버튼 클릭
    const paging = document.querySelector('#pagingNormalOrderDiv') || document.querySelector('.paging') || document.querySelector('[class*="paging"]')
    if (!paging) return { ok: false }
    const cur = paging.querySelector('.selected')
    const curNum = cur ? parseInt(cur.textContent.trim()) : 1
    const next = Array.from(paging.querySelectorAll('button, a')).find(el => parseInt(el.textContent.trim()) === curNum + 1)
    if (next) { next.click(); await sleep(2500); return { ok: true } }
    const nextGroup = Array.from(paging.querySelectorAll('button, a')).find(el => {
      const t = el.textContent.trim()
      return t === '다음' || t === '>' || t === '›' || (el.className || '').includes('next')
    })
    if (nextGroup && nextGroup.offsetHeight > 0) { nextGroup.click(); await sleep(2500); return { ok: true } }
    return { ok: false }
  }

  window.__sambaAbcmartReviewListener = (msg, _s, sr) => {
    const a = msg && msg.action
    if (!['samba_review_ping', 'samba_review_processOne', 'samba_review_loadMore', 'samba_review_getPageInfo'].includes(a)) return
    ;(async () => {
      try {
        if (a === 'samba_review_ping') sr({ loaded: true })
        else if (a === 'samba_review_getPageInfo') sr({ itemCount: getReviewableItems().length })
        else if (a === 'samba_review_processOne') sr(await processOne())
        else if (a === 'samba_review_loadMore') sr(await loadMore())
      } catch (e) { sr({ success: false, error: e.message }) }
    })()
    return true
  }
  chrome.runtime.onMessage.addListener(window.__sambaAbcmartReviewListener)

  console.log('[삼바-ABC마트리뷰] 로드')
})()
