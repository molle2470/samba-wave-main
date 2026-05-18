// content-review-gs.js — GS샵 리뷰 자동작성 (kream-auto-review content-gs.js 포팅)
;(() => {
  if (window.__sambaGsReviewLoaded) return
  window.__sambaGsReviewLoaded = true

  const SAMPLES = [
    '색상도 예쁘고 품질도 좋아서 정말 만족스러운 구매였습니다',
    '배송도 빠르고 상품도 생각보다 훨씬 좋아요 강력 추천합니다',
    '가격 대비 품질이 훌륭해서 재구매 의사 충분히 있습니다',
    '생각보다 훨씬 좋은 품질이에요 오래 사용할 수 있을 것 같아요',
    '사이즈도 딱 맞고 소재 질도 좋아서 매우 만족합니다',
    '가성비 정말 좋고 디자인도 마음에 들어서 주변에 추천했어요',
    '기대 이상으로 만족스러운 제품이에요 다음에도 또 구매할게요',
    '포장도 깔끔하고 상품 상태가 완벽해서 기분 좋게 받았습니다',
    '빠른 배송 감사해요 물건 품질도 좋고 전반적으로 만족합니다',
    '마감도 꼼꼼하고 전체적으로 완성도 높은 상품이라 만족스러워요',
    '편하게 잘 사용하고 있어요 품질도 좋고 실용적이라 추천합니다',
    '착용감이 좋고 소재도 부드러워서 오래 사용할 것 같습니다',
    '컬러가 화면과 동일하고 품질도 좋아서 만족스러운 쇼핑이었어요',
    '다음에도 또 구매하고 싶을 만큼 마음에 드는 제품이에요',
    '선물용으로 샀는데 받는 분도 너무 좋아하셔서 뿌듯합니다',
    '실물이 사진보다 더 예뻐서 놀랐어요 품질도 훌륭합니다',
    '여러번 사용해도 변형 없이 잘 유지돼서 내구성이 좋습니다',
    '합리적인 가격에 이 정도 품질이면 정말 만족스러운 구매입니다',
    '꼼꼼한 포장 덕분에 상품 손상 없이 깔끔하게 잘 받았습니다',
    '가볍고 편해서 매일 사용하고 있어요 구매하길 잘했습니다',
  ]
  let idx = Math.floor(Math.random() * SAMPLES.length)
  function nextText() { const t = SAMPLES[idx % SAMPLES.length]; idx++; return t }
  function sleep(ms) { return new Promise(r => setTimeout(r, ms)) }
  const isListPage = () => location.href.includes('ord/dlvcursta/ordList')
  const isWritePage = () => location.href.includes('knownew/estimate/estimateWrite')

  function collectItems() {
    const links = document.querySelectorAll('a[href*="goWriteReview"]')
    const items = []
    const seen = new Set()
    links.forEach(a => {
      const href = a.getAttribute('href') || ''
      const m = href.match(/goWriteReview\(\s*(\d+)\s*,\s*'?(\d+)'?\s*,\s*'?(\d+)'?\s*,/)
      if (!m) return
      const key = `${m[1]}-${m[2]}-${m[3]}`
      if (seen.has(key)) return
      seen.add(key)
      const tr = a.closest('tr')
      let hasReturnBtn = false
      if (tr) {
        hasReturnBtn = [...tr.querySelectorAll('a, button')].some(el => el.textContent.trim() === '반품교환신청')
      }
      if (hasReturnBtn) return // 구매확정 안 된 항목 스킵
      const writeUrl = `https://www.gsshop.com/knownew/estimate/estimateWrite.gs?save_root=myreview&prdid=${m[1]}&order_num=${m[2]}&lineNum=${m[3]}&onemmRevwYn=N`
      items.push(writeUrl)
    })
    return items
  }

  function getPageInfo() { return { generalPaths: collectItems() } }

  async function scrollAndCollect() {
    const prevY = window.scrollY
    window.scrollBy(0, 2500)
    await sleep(1500)
    const items = collectItems()
    const atBottom = window.scrollY === prevY && prevY > 100
    return { generalPaths: items, atBottom }
  }

  async function fillAndSubmit() {
    try {
      if (!isWritePage()) return { success: false, error: 'write 페이지 아님' }
      await sleep(1500)

      // 별점 5점
      const sLbl = document.querySelector('label[for="star5"]')
      const sInp = document.querySelector('input#star5')
      if (sLbl) sLbl.click()
      else if (sInp) sInp.click()
      else {
        const r = document.querySelector('input[name="prdrevwTotGrd"][value="5"]')
        if (r) {
          const l = document.querySelector(`label[for="${r.id}"]`)
          ;(l || r).click()
        }
      }
      await sleep(500)

      // 추가 라디오 중간값
      const groups = {}
      document.querySelectorAll('input.objEvalRadio[type=radio]').forEach(r => {
        if (!groups[r.name]) groups[r.name] = []
        groups[r.name].push(r)
      })
      for (const radios of Object.values(groups)) {
        if (radios.some(r => r.checked)) continue
        const mid = radios[Math.floor(radios.length / 2)]
        const lbl = document.querySelector(`label[for="${mid.id}"]`)
        ;(lbl || mid).click()
        await sleep(150)
      }

      const ta = document.querySelector('#eval-write')
        || document.querySelector('textarea[name="eval-write"]')
        || document.querySelector('textarea')
      if (!ta) return { success: false, error: 'textarea 없음' }
      ta.focus()
      ta.value = nextText()
      for (const ev of ['focus', 'input', 'change', 'keyup', 'blur']) ta.dispatchEvent(new Event(ev, { bubbles: true }))
      await sleep(700)

      const submitBtn = document.querySelector('button#registerBtn')
        || document.querySelector('button[class*="registerBtn"]')
        || [...document.querySelectorAll('button[type=submit]')].find(b => b.textContent.includes('등록'))
      if (!submitBtn) return { success: false, error: '등록 버튼 없음' }
      for (let i = 0; i < 30; i++) {
        if (!submitBtn.disabled && !submitBtn.classList.contains('disabled')) break
        await sleep(200)
      }
      if (submitBtn.disabled || submitBtn.classList.contains('disabled')) {
        return { success: false, error: '등록 버튼 비활성' }
      }
      submitBtn.click()
      await sleep(1800)
      // 사진/동영상 모달 → 등록하기
      for (let i = 0; i < 15; i++) {
        const b = document.querySelector('#alertConfirm2')
        if (b && b.offsetParent !== null) { b.click(); await sleep(1500); break }
        await sleep(300)
      }
      // 적립 완료 모달 → 확인
      for (let i = 0; i < 15; i++) {
        const b = document.querySelector('#alertConfirm1')
        if (b && b.offsetParent !== null) { b.click(); await sleep(800); break }
        await sleep(300)
      }
      return { success: true }
    } catch (e) {
      return { success: false, error: e.message }
    }
  }

  chrome.runtime.onMessage.addListener((msg, _s, sr) => {
    const a = msg && msg.action
    if (a === 'samba_review_ping') { sr({ loaded: true }); return true }
    ;(async () => {
      try {
        if (a === 'samba_review_getPageInfo') sr(getPageInfo())
        else if (a === 'samba_review_scrollAndCollect') sr(await scrollAndCollect())
        else if (a === 'samba_review_fillAndSubmit') sr(await fillAndSubmit())
        else sr({ error: 'unknown' })
      } catch (e) { sr({ success: false, error: e.message }) }
    })()
    return true
  })

  console.log('[삼바-GS리뷰] 로드:', isListPage() ? 'LIST' : isWritePage() ? 'WRITE' : 'OTHER')
})()
