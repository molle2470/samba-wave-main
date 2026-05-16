// content-review-naver.js — 네이버 리뷰 자동작성 (kream-auto-review content-naver.js 포팅)
// list (shopping.naver.com/my/writable-reviews) → 버튼 클릭 시 window.open으로 팝업
// popup (/popup/reviews/form|monthly-form) — 폼 채우기 + 제출
// inplace 모드: list 페이지에서 processOne 호출 시 클릭 후 팝업 탭 열림.
//   같은 content script가 팝업 페이지에도 주입되어 자동으로 fillAndSubmit 실행.
;(() => {
  if (window.__sambaNaverReviewLoaded) return
  window.__sambaNaverReviewLoaded = true

  const SAMPLES = [
    '색상도 예쁘고 품질도 좋아서 만족합니다',
    '배송도 빠르고 상품도 좋아요 추천합니다',
    '가격 대비 품질이 좋아서 재구매 의사 있어요',
    '생각보다 훨씬 좋은 품질이에요 만족스럽습니다',
    '사이즈도 딱 맞고 질도 좋아서 좋습니다',
    '가성비 좋고 디자인도 마음에 들어요 추천해요',
    '기대 이상으로 만족스러운 제품이에요 좋습니다',
    '포장도 깔끔하고 상품 상태가 완벽합니다',
    '빠른 배송 감사해요 물건도 좋습니다 만족해요',
    '마감도 꼼꼼하고 전체적으로 만족하는 상품이에요',
    '편하게 잘 사용하고 있어요 감사합니다 추천',
    '착용감이 좋고 소재도 부드러워서 좋아요',
    '컬러가 화면과 동일하고 품질도 좋습니다',
    '다음에도 또 구매하고 싶은 제품이에요 만족',
    '선물용으로 샀는데 받는 분도 만족하셨어요',
    '실물이 사진보다 더 예쁜 것 같아요 좋아요',
    '여러번 사용해도 변형 없이 잘 유지돼요 좋아요',
    '합리적인 가격에 좋은 품질이라 만족합니다',
    '꼼꼼한 포장 덕분에 상품 손상 없이 받았어요',
    '가볍고 편해서 매일 사용하고 있습니다 만족해요',
  ]
  let idx = Math.floor(Math.random() * SAMPLES.length)
  function nextText() { const t = SAMPLES[idx % SAMPLES.length]; idx++; return t }
  function sleep(ms) { return new Promise(r => setTimeout(r, ms)) }

  const isListPage = () => location.href.includes('shopping.naver.com/my/writable-reviews') || location.href.includes('order.pay.naver.com/home/review/reviewable')
  const isPopupPage = () => location.href.includes('/popup/reviews/form') || location.href.includes('/popup/reviews/monthly-form')

  function getReviewButtons() {
    const cands = [...document.querySelectorAll('[data-shp-area-id="rvwrite"]')]
    return cands.filter(b => {
      const t = b.textContent.trim()
      return (t === '리뷰쓰기' || t === '한달리뷰쓰기') && !b.dataset.sambaReviewed
    })
  }

  async function fillAndSubmit() {
    try {
      await sleep(1500)
      // 별점 5점 (DOM 역순 배열 — [0]이 5점)
      const stars = [...document.querySelectorAll('[class*="rating_button_grade"]')].filter(el => el.tagName === 'BUTTON')
      if (stars.length > 0) {
        stars[0].click()
        await sleep(800)
      }

      // 라디오 그룹 마지막 옵션
      await sleep(1200)
      const radios = [...document.querySelectorAll('input[type="radio"]')]
      if (radios.length > 0) {
        const groups = {}
        radios.forEach(r => {
          const k = r.name || r.closest('[role="radiogroup"], fieldset, ul, ol')?.className || 'g'
          if (!groups[k]) groups[k] = []
          groups[k].push(r)
        })
        for (const rs of Object.values(groups)) {
          if (rs.some(r => r.checked)) continue
          const last = rs[rs.length - 1]
          const lbl = (last.id ? document.querySelector(`label[for="${last.id}"]`) : null) || last.closest('label')
          ;(lbl || last).click()
          await sleep(250)
        }
      } else {
        const aria = [...document.querySelectorAll('[role="radio"]')]
        if (aria.length > 0) {
          const groups = []; let prev = null
          aria.forEach(el => {
            const p = el.closest('[role="radiogroup"]') || el.parentElement
            if (p !== prev) { groups.push([]); prev = p }
            groups[groups.length - 1].push(el)
          })
          for (const g of groups) {
            if (g.some(el => el.getAttribute('aria-checked') === 'true')) continue
            g[g.length - 1].click()
            await sleep(250)
          }
        }
      }

      // 텍스트
      const ta = document.querySelector('#reviewInput')
        || document.querySelector('[class*="reviewTextArea"][class*="textarea"]')
        || document.querySelector('[class*="input_textarea"]')
        || document.querySelector('textarea')
      if (!ta) return { success: false, error: 'textarea 없음' }
      ta.focus(); await sleep(200)
      const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set
      setter.call(ta, nextText())
      for (const ev of ['focus', 'input', 'change', 'keyup', 'blur']) ta.dispatchEvent(new Event(ev, { bubbles: true }))
      await sleep(900)

      // 제출
      const submit = document.querySelector('[class*="reviewSubmitButton"][class*="button_submit"]')
        || document.querySelector('[class*="button_submit"]')
        || [...document.querySelectorAll('button')].find(b => ['등록', '작성완료', '리뷰 등록'].includes(b.textContent.trim()))
      if (!submit) return { success: false, error: '제출 버튼 없음' }
      for (let i = 0; i < 30; i++) {
        if (!submit.disabled) break
        await sleep(200)
      }
      if (submit.disabled) return { success: false, error: '제출 버튼 비활성' }
      submit.click()
      await sleep(2500)
      // 팝업 자동 닫기
      try { window.close() } catch {}
      return { success: true }
    } catch (e) {
      return { success: false, error: e.message }
    }
  }

  async function processOne() {
    if (isPopupPage()) {
      // 팝업 자동주입 케이스 — 폼 채우고 제출 후 윈도우 닫힘
      return await fillAndSubmit()
    }
    if (!isListPage()) return { success: false, error: `네이버 list 페이지 아님: ${location.pathname}` }
    const btns = getReviewButtons()
    if (btns.length === 0) return { noItems: true }
    const btn = btns[0]
    btn.dataset.sambaReviewed = 'true'
    // 버튼 클릭 — 새 탭에 팝업 열림 (background 가 onUpdated로 감지하지 못하더라도
    //  같은 도메인 팝업은 manifest 자동주입 가능, 또는 background에서 별도 처리)
    btn.click()
    // 팝업이 fillAndSubmit 후 close까지 자동 처리 — list 페이지는 5초 대기
    await sleep(8000)
    return { success: true } // 팝업 결과는 직접 알 수 없음. 낙관적 성공 처리
  }

  async function loadMore() {
    // 네이버는 페이지 하단으로 스크롤하면 자동 로드 (또는 페이지네이션 버튼)
    window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' })
    await sleep(2500)
    return { ok: true }
  }

  chrome.runtime.onMessage.addListener((msg, _s, sr) => {
    const a = msg && msg.action
    if (a === 'samba_review_ping') { sr({ loaded: true, popup: isPopupPage(), list: isListPage() }); return true }
    ;(async () => {
      try {
        if (a === 'samba_review_getPageInfo') sr({ itemCount: getReviewButtons().length })
        else if (a === 'samba_review_processOne') sr(await processOne())
        else if (a === 'samba_review_fillAndSubmit') sr(await fillAndSubmit())
        else if (a === 'samba_review_loadMore') sr(await loadMore())
        else sr({ error: 'unknown' })
      } catch (e) { sr({ success: false, error: e.message }) }
    })()
    return true
  })

  // 팝업으로 열린 경우 자동 실행
  if (isPopupPage()) {
    setTimeout(() => fillAndSubmit().catch(() => {}), 1500)
  }

  console.log('[삼바-네이버리뷰] 로드:', isListPage() ? 'LIST' : isPopupPage() ? 'POPUP' : 'OTHER')
})()
