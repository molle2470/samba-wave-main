// content-review-kream.js — KREAM 리뷰 자동작성 (kream-auto-review content/content.js 포팅)
// 인페이지 패턴 — 별점 클릭 → 자동구매확정 팝업 → 리뷰 모달 → 칩 선택 → 제출
;(() => {
  if (window.__sambaKreamReviewLoaded) return
  window.__sambaKreamReviewLoaded = true

  const DEFAULT_WEIGHTS = {
    starWeights: { 5: 70, 4: 25, 3: 5 },
    sizeEvalWeights: { 정사이즈예요: 80, '작게 나왔어요': 10, '크게 나왔어요': 10 },
    sizeCompareWeights: { 정사이즈: 70, '반사이즈 다운': 15, '반사이즈 업': 15 },
    comfortWeights: { 편해요: 60, 보통이에요: 30, 불편해요: 10 },
    widthWeights: { 보통이에요: 60, 넓어요: 25, 좁아요: 15 },
  }
  const W = DEFAULT_WEIGHTS

  function weightedChoice(w) {
    const entries = Object.entries(w)
    const total = entries.reduce((s, [, v]) => s + v, 0)
    let r = Math.random() * total
    for (const [c, v] of entries) { r -= v; if (r <= 0) return c }
    return entries[entries.length - 1][0]
  }
  function sleep(ms) { return new Promise(r => setTimeout(r, ms)) }
  function rand(a, b) { return Math.floor(Math.random() * (b - a + 1)) + a }
  function click(el) { if (el) el.click() }

  function waitFor(fn, timeout = 5000) {
    return new Promise(resolve => {
      const ex = fn()
      if (ex) { resolve(ex); return }
      const ob = new MutationObserver(() => { const el = fn(); if (el) { ob.disconnect(); resolve(el) } })
      ob.observe(document.body, { childList: true, subtree: true })
      setTimeout(() => { ob.disconnect(); resolve(null) }, timeout)
    })
  }

  function getReviewItems() {
    return Array.from(document.querySelectorAll('.product_list_info_summary')).filter(
      it => !it.closest('[class*="review-write"]') && !it.closest('[class*="layer"]') && !it.dataset.sambaReviewed
    )
  }

  function findStarArea(itemEl) {
    const parent = itemEl.closest('[class*="layout_list_vertical"]')
    if (!parent) return null
    return parent.querySelector('[class*="rating"]') || null
  }

  function clickStar(starArea, count) {
    const stars = starArea.querySelectorAll('.rating-element')
    if (stars.length >= count) {
      const t = stars[count - 1]
      const inner = t.querySelector('[class*="image-element"]') || t
      click(inner); return
    }
    click(starArea)
  }

  function findPopupByText(text) {
    const layers = document.querySelectorAll('.layer_container, [class*="layer_container"]')
    for (const l of layers) if (l.textContent.includes(text) && l.querySelector('button')) return l
    for (const d of document.querySelectorAll('div')) {
      if (d.textContent.includes(text) && d.querySelector('button')
          && d.children.length <= 10 && d.offsetWidth > 200 && d.offsetWidth < 600) return d
    }
    return null
  }

  function findQuestionText(box) {
    let el = box.previousElementSibling
    while (el) {
      const t = el.textContent.trim()
      if (t.length > 3 && t.length < 100 && t.includes('?')) return t
      const p = el.querySelector('p')
      if (p && p.textContent.trim().length > 3) return p.textContent.trim()
      el = el.previousElementSibling
    }
    return ''
  }

  function pickChip(question, chips) {
    let w
    if (question.includes('사이즈는 어떤가요') || question.includes('구매하신')) w = W.sizeEvalWeights
    else if (question.includes('크게/작게') || question.includes('평소 사이즈에서')) w = W.sizeCompareWeights
    else if (question.includes('착화감')) w = W.comfortWeights
    else if (question.includes('발볼')) w = W.widthWeights
    else return chips[Math.min(1, chips.length - 1)]
    const chosen = weightedChoice(w)
    return Array.from(chips).find(c => c.textContent.trim() === chosen) || chips[0]
  }

  async function fillForm(modal) {
    const container = modal.closest('[class*="layer"]') || modal
    const boxes = container.querySelectorAll('[class*="layout_flow_box"]')
    for (const box of boxes) {
      const chips = box.querySelectorAll('[class*="chip_button"]')
      if (chips.length === 0) continue
      const q = findQuestionText(box)
      const chosen = pickChip(q, chips)
      if (chosen) {
        await sleep(rand(700, 1800))
        chosen.scrollIntoView({ behavior: 'instant', block: 'center' })
        await sleep(300)
        click(chosen)
      }
    }
  }

  function findSubmitButton() {
    const footer = document.querySelector('[class*="review-write_footer"]')
    if (footer) {
      const btn = footer.querySelector('button')
      if (btn) return btn
    }
    return Array.from(document.querySelectorAll('button')).find(b => b.textContent.includes('리뷰 제출하기')) || null
  }

  function getLastButton(c) {
    const btns = c.querySelectorAll('button')
    return btns.length > 0 ? btns[btns.length - 1] : null
  }

  async function closeAllModals() {
    for (const btn of document.querySelectorAll('[class*="layer"] button')) {
      if (btn.querySelector('svg') || ['취소', 'X'].includes(btn.textContent.trim())) {
        click(btn); await sleep(300)
      }
    }
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await sleep(400)
  }

  async function processOne() {
    const items = getReviewItems()
    if (items.length === 0) return { noItems: true }
    const itemEl = items[0]
    itemEl.dataset.sambaReviewed = 'true'

    itemEl.scrollIntoView({ behavior: 'instant', block: 'center' })
    await sleep(1000)

    const starArea = findStarArea(itemEl)
    if (!starArea) return { success: false, error: '별점 영역 못 찾음' }

    const starCount = parseInt(weightedChoice(W.starWeights))
    let opened = false
    for (let att = 0; att < 3; att++) {
      clickStar(starArea, starCount)
      const r = await waitFor(() =>
        findPopupByText('자동 구매 확정') || document.querySelector('[class*="review-write_content"]'),
        5000
      )
      if (r) { opened = true; break }
      await sleep(1300)
    }
    if (!opened) return { success: false, error: '별점 클릭 3회 실패' }

    // 자동 구매 확정 팝업
    const alertPopup = findPopupByText('자동 구매 확정')
    if (alertPopup) {
      const c = getLastButton(alertPopup)
      if (c) { await sleep(rand(500, 1300)); click(c); await sleep(rand(1500, 2800)) }
    }

    const modal = await waitFor(() => document.querySelector('[class*="review-write_content"]'), 10000)
    if (!modal) return { success: false, error: '리뷰 모달 안 열림' }

    await fillForm(modal)
    await sleep(rand(700, 1800))

    const submit = findSubmitButton()
    if (!submit) return { success: false, error: '제출 버튼 못 찾음' }
    click(submit)

    const confirm = await waitFor(() => findPopupByText('제출 후에는'), 5000)
    if (confirm) {
      const f = getLastButton(confirm)
      if (f) { await sleep(rand(400, 1200)); click(f) }
    }
    await sleep(rand(1800, 3500))
    await closeAllModals()
    return { success: true, starCount }
  }

  async function loadMore() {
    // KREAM은 페이지 하단 자동 로드 — 스크롤
    window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' })
    await sleep(2500)
    return { ok: getReviewItems().length > 0 }
  }

  chrome.runtime.onMessage.addListener((msg, _s, sr) => {
    const a = msg && msg.action
    if (a === 'samba_review_ping') { sr({ loaded: true }); return true }
    ;(async () => {
      try {
        if (a === 'samba_review_getPageInfo') sr({ itemCount: getReviewItems().length })
        else if (a === 'samba_review_processOne') sr(await processOne())
        else if (a === 'samba_review_loadMore') sr(await loadMore())
        else sr({ error: 'unknown' })
      } catch (e) { sr({ success: false, error: e.message }) }
    })()
    return true
  })

  console.log('[삼바-크림리뷰] 로드')
})()
