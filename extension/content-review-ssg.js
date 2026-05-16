// content-review-ssg.js — SSG 리뷰 자동작성 (kream-auto-review content-ssg.js 포팅)
// list(pdtEvalList) → gate(myPdtEvalRegGate) → reg(myPdtEvalReg) 3단 페이지
;(() => {
  if (window.__sambaSsgReviewLoaded) return
  window.__sambaSsgReviewLoaded = true

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
    '여러번 세탁해도 변형 없이 잘 유지돼요 좋아요',
    '합리적인 가격에 좋은 품질이라 만족합니다',
    '꼼꼼한 포장 덕분에 상품 손상 없이 받았어요',
    '가볍고 편해서 매일 사용하고 있습니다 만족해요',
  ]
  let idx = Math.floor(Math.random() * SAMPLES.length)
  function nextText() { const t = SAMPLES[idx % SAMPLES.length]; idx++; return t }
  function sleep(ms) { return new Promise(r => setTimeout(r, ms)) }

  const isListPage = () => location.href.includes('review.ssg') || location.href.includes('pdtEvalList.ssg')
  const isRegPage = () => location.href.includes('myPdtEvalReg.ssg')
  const isGatePage = () => location.href.includes('myPdtEvalRegGate.ssg')

  function getReviewItems() {
    const btns = document.querySelectorAll('a.review_lst_table_unit_btn')
    const items = []
    const seen = new Set()
    btns.forEach(btn => {
      const href = btn.getAttribute('href') || ''
      const m = href.match(/fn_save\(\s*'([^']*)'\s*,\s*'([^']*)'\s*,\s*'([^']*)'\s*,\s*'([^']*)'\s*,\s*'([^']*)'\s*,\s*'([^']*)'\s*\)/)
      if (!m) return
      const itemId = m[3]
      if (seen.has(itemId)) return
      seen.add(itemId)
      items.push({
        ordNo: m[1], ordItemSeq: m[2], itemId, uitemId: m[4], postngClsCd: m[5], promId: m[6],
      })
    })
    return items
  }

  function buildGateUrl(it) {
    const params = new URLSearchParams({
      ordNo: it.ordNo, ordItemSeq: it.ordItemSeq, itemId: it.itemId,
      uitemId: it.uitemId, postngClsCd: it.postngClsCd, promId: it.promId || '',
    })
    return `https://www.ssg.com/myssg/popup/myPdtEvalRegGate.ssg?${params.toString()}`
  }

  function getPageInfo() {
    const items = getReviewItems()
    return { generalPaths: items.map(buildGateUrl) }
  }

  async function scrollAndCollect() {
    const prevY = window.scrollY
    window.scrollBy(0, 2500)
    await sleep(1500)
    const items = getReviewItems()
    const atBottom = window.scrollY === prevY && prevY > 100
    return { generalPaths: items.map(buildGateUrl), atBottom }
  }

  async function fillAndSubmit() {
    try {
      // gate 페이지: 5점 클릭 → reg로 자동 이동
      if (isGatePage()) {
        await sleep(1000)
        const stars = document.querySelectorAll('button.rating_star_check')
        for (const s of stars) {
          if (s.textContent.includes('5점')) { s.click(); break }
        }
        if (stars.length > 0 && !document.querySelector('button.rating_star_check.on')) {
          stars[stars.length - 1].click()
        }
        await sleep(2500)
        // reg 페이지로 자동 이동되면 이 스크립트는 종료, background가 재주입
        return { success: false, error: 'gate→reg 이동 대기 중', _navigating: true }
      }

      if (!isRegPage()) return { success: false, error: `reg 페이지 아님: ${location.pathname}` }

      await sleep(1500)
      const ta = document.querySelector('#ui_textarea')
        || document.querySelector('textarea[name="postngCntt"]')
        || document.querySelector('textarea')
      if (!ta) return { success: false, error: 'textarea 없음' }
      ta.focus()
      ta.value = nextText()
      for (const ev of ['focus', 'input', 'change', 'keyup', 'blur']) {
        ta.dispatchEvent(new Event(ev, { bubbles: true }))
      }
      await sleep(400)

      // 슬라이더 라디오 (사이즈/색상/착용감) — 중간 옵션
      const circleGroups = {}
      document.querySelectorAll('input[name^="circleRadioTest"]').forEach(r => {
        if (!circleGroups[r.name]) circleGroups[r.name] = []
        circleGroups[r.name].push(r)
      })
      for (const radios of Object.values(circleGroups)) {
        if (radios.some(r => r.checked)) continue
        const mid = radios[Math.floor(radios.length / 2)]
        const lbl = document.querySelector('label[for="' + mid.id + '"]')
        ;(lbl || mid).click()
        await sleep(150)
      }

      // 선물 해시태그 라디오
      for (const sec of document.querySelectorAll('.myreview_sec.ty_present.top_area')) {
        const radios = [...sec.querySelectorAll('input[type=radio]')]
        if (!radios.length || radios.some(r => r.checked)) continue
        const pick = radios.find(r => {
          const lbl = document.querySelector('label[for="' + r.id + '"]') || r.closest('label')
          return !(lbl?.textContent || '').includes('직접입력')
        }) || radios[0]
        const lbl = document.querySelector('label[for="' + pick.id + '"]') || pick.closest('label')
        ;(lbl || pick).click()
        await sleep(150)
      }

      // 별점 확인
      if (document.querySelectorAll('button.rating_star_check.on').length === 0) {
        const allStars = document.querySelectorAll('button.rating_star_check')
        for (const s of allStars) if (s.textContent.includes('5점')) { s.click(); break }
        await sleep(400)
      }

      const submitBtn = document.querySelector('#submitBtn')
        || document.querySelector('button.myreview_btn')
        || document.querySelector('[class*=submit]')
      if (!submitBtn) return { success: false, error: '등록 버튼 없음' }
      submitBtn.click()
      await sleep(3000)
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

  console.log('[삼바-SSG리뷰] 로드:', isListPage() ? 'LIST' : isRegPage() ? 'REG' : isGatePage() ? 'GATE' : 'OTHER')
})()
