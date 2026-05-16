// content-review-musinsa.js
// 무신사 리뷰 자동작성 — kream-auto-review content-musinsa.js 포팅
// /mypage/myreview → general 리뷰 작성 가능 목록 파싱 (가상 스크롤)
// /mypage/myreview/write/general/{id} → 별점/라디오/텍스트/동의/등록
;(() => {
  if (window.__sambaMusinsaReviewLoaded) return
  window.__sambaMusinsaReviewLoaded = true

  const REVIEW_SAMPLES = [
    '착용감이 편하고 디자인도 마음에 들어요 정말 추천합니다',
    '배송도 빠르고 상품도 너무 좋아요 재구매 의사가 있어요',
    '가격 대비 품질이 정말 좋아서 꼭 재구매 할 것 같아요',
    '생각보다 훨씬 더 좋은 품질이에요 정말 매우 만족합니다',
    '사이즈도 딱 맞고 소재도 좋아서 정말로 매우 만족합니다',
    '가성비 너무 좋고 디자인도 예뻐요 정말 강력 추천합니다',
    '기대 이상으로 만족스러운 제품이에요 정말로 너무 좋아요',
    '포장도 아주 깔끔하고 상품 상태가 완벽해요 정말 감사해요',
    '빠른 배송 정말 감사드려요 물건도 너무 좋아요 만족해요',
    '마감도 꼼꼼하고 전체적으로 만족스러운 상품이에요 추천해요',
    '편하게 잘 사용하고 있어요 정말 감사합니다 꼭 추천해요',
    '착용감이 너무 좋고 소재도 부드러워서 정말 너무 좋습니다',
    '컬러가 사진과 거의 동일하고 품질도 정말 매우 좋습니다',
    '다음에도 또 구매하고 싶은 정말 좋은 제품이에요 만족해요',
    '선물용으로 샀는데 받는 분이 정말 너무 매우 만족하셨어요',
    '실물이 사진보다 훨씬 더 예쁜 것 같아요 정말 너무 좋아요',
    '여러번 사용해도 변형 없이 잘 유지돼요 정말 너무 좋습니다',
    '합리적인 가격에 아주 좋은 품질이라 정말 매우 만족합니다',
    '꼼꼼한 포장 덕분에 상품 손상 없이 잘 받았어요 감사해요',
    '가볍고 편해서 매일 사용하고 있어요 정말 너무 만족합니다',
  ]

  let reviewIdx = Math.floor(Math.random() * REVIEW_SAMPLES.length)
  function nextReviewText() {
    const t = REVIEW_SAMPLES[reviewIdx % REVIEW_SAMPLES.length]
    reviewIdx++
    return t
  }

  function sleep(ms) { return new Promise(r => setTimeout(r, ms)) }

  function pointerClick(el) {
    if (!el) return
    const r = el.getBoundingClientRect()
    const cx = r.x + r.width / 2
    const cy = r.y + r.height / 2
    for (const type of ['pointerdown', 'pointerup', 'click']) {
      el.dispatchEvent(new PointerEvent(type, {
        bubbles: true, cancelable: true, clientX: cx, clientY: cy,
        pointerId: 1, isPrimary: true, view: window,
      }))
    }
  }

  const SKIP_KEYWORDS = ['상품권', '기프트카드', '기프트 카드', 'gift card']
  function getGeneralReviewPaths() {
    const links = [...document.querySelectorAll('a[href*="/mypage/myreview/write/general/"]')]
    const paths = links.map(a => {
      try {
        const container = a.closest('[class*="item"]') || a.closest('li') || a.closest('div')?.parentElement
        const text = container?.textContent || ''
        if (SKIP_KEYWORDS.some(k => text.includes(k))) return null
        return new URL(a.href).pathname
      } catch { return null }
    }).filter(Boolean)
    return [...new Set(paths)]
  }

  async function scrollAndCollect() {
    const prevY = window.scrollY
    window.scrollBy(0, 3000)
    await sleep(2000)
    const paths = getGeneralReviewPaths()
    const newHeight = document.body.scrollHeight
    const didntMove = window.scrollY === prevY && prevY > 200
    const reachedBottom = window.scrollY + window.innerHeight >= newHeight - 200
    return { generalPaths: paths, atBottom: didntMove || reachedBottom }
  }

  async function fillAndSubmit() {
    try {
      await sleep(400)

      // 1. 별점 5점
      const stars = document.querySelectorAll('[class*="StarScore__StarWrapper"] svg')
      if (stars.length > 0) {
        pointerClick(stars[stars.length - 1])
        await sleep(50)
      }

      // 2. 라디오 그룹별 중간값 (index 2)
      const groups = document.querySelectorAll('[class*="Answer__AnswerGroup"]')
      for (const g of groups) {
        const circles = g.querySelectorAll(
          'button[class*="Answer__EmptyCircle"], button[class*="Answer__FilledCircle"]'
        )
        if (circles.length >= 3) {
          circles[2].click()
          await sleep(100)
        }
      }

      // 3. 텍스트 입력
      const ta = document.querySelector('textarea')
      if (ta) {
        const text = nextReviewText()
        const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set
        setter.call(ta, text)
        ta.dispatchEvent(new Event('input', { bubbles: true }))
        ta.dispatchEvent(new Event('change', { bubbles: true }))
        ta.focus()
        await sleep(200)
      }

      // 4. 전체 동의
      const cbBtns = document.querySelectorAll('button[role="checkbox"]')
      let agreeBtn = null
      for (const b of cbBtns) {
        const lbl = b.nextElementSibling?.textContent?.trim() || ''
        if (lbl.includes('전체 동의')) { agreeBtn = b; break }
      }
      if (agreeBtn && agreeBtn.getAttribute('data-state') !== 'checked') {
        pointerClick(agreeBtn)
        await sleep(300)
      }

      // 5. 등록하기
      const submitBtn = [...document.querySelectorAll('button')].find(b => b.textContent.trim() === '등록하기')
      if (!submitBtn) return { success: false, error: '등록하기 버튼 없음' }
      submitBtn.click()

      // 6. 성공 감지
      for (let i = 0; i < 30; i++) {
        await sleep(500)
        if (location.pathname.includes('/mypage/myreview/done/')) return { success: true }
        const okBtn = [...document.querySelectorAll('button')].find(b => b.textContent.trim() === '확인')
        if (okBtn) { okBtn.click(); return { success: true } }
      }
      return { success: false, error: '등록 결과 확인 실패 (타임아웃)' }
    } catch (e) {
      return { success: false, error: e.message }
    }
  }

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    const action = msg && msg.action
    if (action === 'samba_review_ping') { sendResponse({ loaded: true }); return true }
    ;(async () => {
      try {
        switch (action) {
          case 'samba_review_getPageInfo':
            sendResponse({ generalPaths: getGeneralReviewPaths() }); break
          case 'samba_review_scrollAndCollect':
            sendResponse(await scrollAndCollect()); break
          case 'samba_review_fillAndSubmit':
            sendResponse(await fillAndSubmit()); break
          default:
            sendResponse({ error: `unknown action: ${action}` })
        }
      } catch (e) {
        sendResponse({ success: false, error: e.message })
      }
    })()
    return true
  })

  console.log('[삼바-무신사리뷰] content script 로드')
})()
