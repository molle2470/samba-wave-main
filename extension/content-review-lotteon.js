// content-review-lotteon.js — 롯데ON 리뷰 자동작성 (kream-auto-review content-lotteon.js 포팅)
// Vue.js 모달 인페이지 패턴 — 리뷰쓰기 버튼 클릭 → 모달 → 폼 채우기 → 제출 → 모달 닫힘
;(() => {
  if (window.__sambaLotteonReviewListener) {
    try { chrome.runtime.onMessage.removeListener(window.__sambaLotteonReviewListener) } catch {}
  }

  const SAMPLES = [
    '편하게 잘 사용하고 있어요 디자인도 예쁘고 매우 만족합니다',
    '색상이 실물로 봐도 너무 예뻐요 품질도 좋고 재구매 의향 있습니다',
    '배송도 빠르고 상품도 깔끔하게 잘 왔어요 마음에 쏙 들어요',
    '착용감이 편하고 디자인이 세련돼서 자주 사용하게 됩니다 만족해요',
    '가볍고 소재가 좋아서 하루 종일 사용해도 불편하지 않아요 추천합니다',
    '색상이 사진과 동일하고 마감 처리도 꼼꼼해서 좋습니다 강추해요',
    '딱 원하던 스타일이에요 핏도 예쁘고 소재도 부드러워서 만족합니다',
    '예상보다 훨씬 마음에 들어요 코디하기도 편하고 활용도가 높아요',
    '처음엔 고민했는데 역시 구매하길 잘했습니다 너무 마음에 들어요',
    '품질 대비 가격이 정말 좋아요 주변에도 적극 추천하고 싶습니다',
    '발림성이 좋고 촉촉하게 마무리돼서 매일 사용하게 되는 제품이에요',
    '향이 은은하고 자극 없이 순해서 민감한 피부에도 좋아요 재구매 의사 있어요',
    '마무리감이 깔끔하고 오래 지속되는 것 같아서 아주 만족합니다',
    '피부 톤이 화사해지는 느낌이에요 발색도 자연스럽고 좋아서 마음에 들어요',
    '사용하면 할수록 더 마음에 들어요 앞으로도 계속 쓸 것 같습니다',
    '선물로 샀는데 받은 분도 아주 좋아하셨어요 포장도 깔끔했습니다',
    '포장 상태도 좋고 상품도 기대 이상이었어요 재구매할 생각입니다',
    '사용 후 피부가 촉촉해지는 느낌이 들어서 매우 만족해요 강추합니다',
    '착용감이 생각보다 더 편하고 디자인도 데일리로 활용하기 정말 좋아요',
    '소재가 고급스럽고 마무리가 꼼꼼해서 오래 쓸 수 있을 것 같아요',
  ]
  function randomText() { return SAMPLES[Math.floor(Math.random() * SAMPLES.length)] }
  function sleep(ms) { return new Promise(r => setTimeout(r, ms)) }
  function rand(a, b) { return Math.floor(Math.random() * (b - a + 1)) + a }

  function getReviewableButtons() {
    return Array.from(document.querySelectorAll('button')).filter(b => {
      if (b.dataset.sambaReviewed) return false
      return b.textContent.trim().startsWith('리뷰쓰기')
    })
  }

  function waitForModal(timeout = 7000) {
    return new Promise(resolve => {
      const check = (el) => {
        const m = document.querySelector('.v--modal-box')
        if (!m) return null
        const pc = m.querySelector('.popContents')
        if (pc && pc.classList.contains('review-completion')) return null
        return (el < 2000 || m.offsetHeight > 0) ? m : null
      }
      const ex = check(0)
      if (ex) { resolve(ex); return }
      let el = 0
      const poll = setInterval(() => {
        el += 200
        const m = check(el)
        if (m) { clearInterval(poll); resolve(m) }
        else if (el >= timeout) { clearInterval(poll); resolve(null) }
      }, 200)
    })
  }

  function waitForModalClose(modal, timeout = 10000) {
    return new Promise(resolve => {
      let el = 0
      const poll = setInterval(async () => {
        el += 200
        if (!document.body.contains(modal) || modal.offsetHeight === 0) {
          clearInterval(poll); resolve(true); return
        }
        const pc = modal.querySelector('.popContents')
        if (pc && pc.classList.contains('review-completion')) {
          clearInterval(poll)
          const closeBtn = modal.querySelector('.guidClose')
          if (closeBtn) {
            closeBtn.click()
            let w = 0
            while (w < 4000) {
              await sleep(200); w += 200
              if (!document.body.contains(modal) || modal.offsetHeight === 0) break
            }
            if (document.body.contains(modal) && modal.offsetHeight > 0) {
              modal.style.display = 'none'
              const overlay = document.querySelector('.v--overlay')
              if (overlay) overlay.style.display = 'none'
            }
          }
          resolve(true); return
        }
        if (el >= timeout) { clearInterval(poll); resolve(false) }
      }, 200)
    })
  }

  async function processOne() {
    const items = getReviewableButtons()
    if (items.length === 0) return { noItems: true }
    const reviewBtn = items[0]

    // 잔존 모달 정리
    const stale = document.querySelector('.v--modal-box')
    if (stale && stale.offsetHeight > 0) {
      const sc = stale.querySelector('.guidClose') || stale.querySelector('button[class*="close"]')
      if (sc) { sc.click(); await sleep(600) }
      if (stale.offsetHeight > 0) {
        stale.style.display = 'none'
        const ov = document.querySelector('.v--overlay'); if (ov) ov.style.display = 'none'
      }
      await sleep(400)
    }

    reviewBtn.scrollIntoView({ behavior: 'instant', block: 'center' })
    await sleep(rand(500, 1000))
    reviewBtn.click()

    const modal = await waitForModal(7000)
    if (!modal) return { success: false, error: '모달 안 열림' }
    await sleep(rand(500, 1000))

    // 별점 5점
    let starBtns = []
    let sw = 0
    while (sw < 4000) {
      starBtns = Array.from(modal.querySelectorAll('button')).filter(b => /^[1-5]점$/.test(b.textContent.trim()))
      if (starBtns.length > 0) break
      await sleep(300); sw += 300
    }
    const star5 = starBtns.find(b => b.textContent.trim() === '5점')
    if (!star5) {
      reviewBtn.dataset.sambaReviewed = 'true'
      return { success: false, error: '별점 버튼 없음' }
    }
    star5.click()
    await sleep(500)

    // 사이즈/색상 라디오 (중간값)
    for (const box of modal.querySelectorAll('.review-create__evaluation__box')) {
      const labels = box.querySelectorAll('label')
      if (labels.length >= 3) { labels[1].click(); await sleep(rand(200, 400)) }
      else if (labels.length === 2) { labels[0].click(); await sleep(rand(200, 400)) }
    }
    await sleep(rand(300, 600))

    const ta = modal.querySelector('textarea')
    if (!ta) {
      reviewBtn.dataset.sambaReviewed = 'true'
      return { success: false, error: 'textarea 없음' }
    }
    const text = randomText()
    ta.focus(); await sleep(200)
    const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value')?.set
    if (setter) setter.call(ta, text)
    else ta.value = text
    ta.dispatchEvent(new Event('input', { bubbles: true }))
    ta.dispatchEvent(new Event('change', { bubbles: true }))
    await sleep(400)
    if (ta.value.length < 20) {
      ta.select(); document.execCommand('insertText', false, text)
      ta.dispatchEvent(new Event('input', { bubbles: true }))
      await sleep(300)
    }

    // "다음에 할게요" 체크박스 (포토 첨부 패널티 회피)
    const skipLabel = modal.querySelector('label[for="checkNotUse"]')
    const skipCb = modal.querySelector('#checkNotUse')
    if (skipLabel && !skipCb?.checked) {
      skipLabel.click()
      await sleep(rand(300, 500))
    }

    // 제출
    const submit = Array.from(modal.querySelectorAll('button')).find(b => b.textContent.trim() === '리뷰 등록하기')
    if (!submit) {
      reviewBtn.dataset.sambaReviewed = 'true'
      return { success: false, error: '등록 버튼 없음' }
    }
    let we = 0
    while (submit.disabled && we < 5000) {
      await sleep(200); we += 200
      if (we === 2000 && submit.disabled && skipLabel) {
        if (skipCb?.checked) skipLabel.click()
        skipLabel.click()
      }
    }
    submit.click()
    const closed = await waitForModalClose(modal, 8000)
    reviewBtn.dataset.sambaReviewed = 'true'
    return { success: closed, error: closed ? null : '제출 후 모달 안 닫힘' }
  }

  async function loadMore() {
    const btn = document.querySelector('button.btnReadmore')
    if (btn && btn.offsetHeight > 0) { btn.click(); return { ok: true } }
    return { ok: false }
  }

  window.__sambaLotteonReviewListener = (msg, _s, sr) => {
    const a = msg && msg.action
    if (!['samba_review_ping', 'samba_review_processOne', 'samba_review_loadMore', 'samba_review_getPageInfo'].includes(a)) return
    ;(async () => {
      try {
        if (a === 'samba_review_ping') sr({ loaded: true })
        else if (a === 'samba_review_getPageInfo') sr({ itemCount: getReviewableButtons().length })
        else if (a === 'samba_review_processOne') sr(await processOne())
        else if (a === 'samba_review_loadMore') sr(await loadMore())
      } catch (e) { sr({ success: false, error: e.message }) }
    })()
    return true
  }
  chrome.runtime.onMessage.addListener(window.__sambaLotteonReviewListener)

  console.log('[삼바-롯데리뷰] 로드')
})()
