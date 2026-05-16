// content-reward-musinsa-snap.js
// 무신사 스냅 좋아요 미션 자동화 (kream-auto-review 포팅)
// 3단계 (각 단계마다 페이지 이동 → background가 재주입):
//   1) 미션 페이지: "스냅 좋아요 누르러 가기" 클릭 → 스냅 피드 이동
//   2) 스냅 피드: 하트 3개 클릭 → 미션 페이지 복귀
//   3) 미션 페이지(복귀): "적립금 받기" 클릭 → 20원 수령
;(async () => {
  // 단계별 중복 실행 방지 (URL 기준)
  const _flagKey = `__sambaSnapStage_${location.pathname}`
  if (window[_flagKey]) return
  window[_flagKey] = true

  function sleep(ms) { return new Promise(r => setTimeout(r, ms)) }

  function send(extra) {
    return chrome.runtime.sendMessage({
      type: 'REWARD_RESULT',
      rewardAction: 'musinsa_snap_like',
      ...extra,
    }).catch(() => {})
  }

  function log(msg) { console.log('[적립금-스냅]', msg) }

  const url = location.href
  log(`진입: ${url}`)

  // ─── 1단계: 미션 페이지 (snap-daily-like 탭) ───
  if (url.includes('/mission') && (url.includes('snap-daily-like') || url.includes('snap'))) {
    await sleep(2500)
    const bodyText = document.body?.innerText || ''

    if (bodyText.includes('내일 다시 참여') || bodyText.includes('이미 참여')) {
      log('오늘 미션 이미 완료')
      send({ success: true, alreadyDone: true, reward: 0 })
      return
    }

    // 좋아요 완료 후 복귀한 경우 (storage 플래그)
    const stored = await chrome.storage.local.get('__sambaSnapLikesDone')
    if (stored.__sambaSnapLikesDone) {
      log('좋아요 완료 후 복귀 — 적립금 받기 버튼 탐색')
      await chrome.storage.local.remove('__sambaSnapLikesDone')

      let rewardBtn = null
      for (let i = 0; i < 20; i++) {
        rewardBtn = document.querySelector('.CDetailSnapFloatingButton')
          || document.querySelector('[class*="FloatingButton"]')
          || document.querySelector('[class*="floating-button"]')
        const t = rewardBtn?.textContent?.trim() || ''
        if (rewardBtn && !rewardBtn.disabled
            && (t.includes('적립금') || t.includes('받기'))
            && !t.includes('내일') && !t.includes('완료')) break
        rewardBtn = null
        await sleep(500)
      }

      if (!rewardBtn) {
        rewardBtn = Array.from(document.querySelectorAll('button')).find(b => {
          const t = b.textContent?.trim() || ''
          return (t.includes('적립금') || t.includes('받기')) && !t.includes('내일') && !b.disabled
        }) || null
      }

      if (rewardBtn && !rewardBtn.disabled) {
        log(`적립금 받기 클릭 (${rewardBtn.textContent.trim()})`)
        rewardBtn.click()
        await sleep(3000)
        send({ success: true, alreadyDone: false, reward: 20 })
      } else {
        const latest = document.body?.innerText || ''
        if (latest.includes('내일 다시 참여')) {
          send({ success: true, alreadyDone: true, reward: 0 })
        } else {
          send({ success: false, error: '적립금 버튼 못 찾음' })
        }
      }
      return
    }

    // 1차 진입: "스냅 좋아요 누르러 가기" 버튼 클릭
    let goBtn = document.querySelector('.CContentsSnap--v2__button')
      || document.querySelector('[class*="CContentsSnap"]')
      || document.querySelector('[class*="snap"][class*="button"]')

    if (!goBtn) {
      goBtn = Array.from(document.querySelectorAll('button, a')).find(b => {
        const t = b.textContent?.trim() || ''
        return t.includes('스냅') && (t.includes('가기') || t.includes('이동') || t.includes('바로'))
      }) || null
    }

    if (goBtn) {
      log('"스냅 좋아요 누르러 가기" 클릭')
      goBtn.click()
      // URL 변경되면 background가 새 페이지에서 스크립트 재주입
    } else {
      log('진입 버튼 못 찾음')
      send({ success: false, error: 'no_snap_button' })
    }
    return
  }

  // ─── 2단계: 스냅 피드 → 좋아요 3개 ───
  if (url.includes('/snap/main') || url.includes('/snap/recommend') || url.includes('/snap?')) {
    await sleep(3000)
    log('스냅 피드 — 좋아요 3개 시작')

    // 모달 팝업 닫기
    try {
      const popup = document.querySelector('[class*="BannerPopupWrapper"]')
      if (popup) {
        for (const s of popup.querySelectorAll('span')) {
          const t = s.textContent?.trim()
          if (t === '닫기' || t === '오늘 그만보기') { s.click(); break }
        }
        await sleep(800)
      }
    } catch {}

    const TARGET = 3
    let count = 0
    for (let scroll = 0; scroll < 15 && count < TARGET; scroll++) {
      const btns = Array.from(document.querySelectorAll(
        'button[aria-label="좋아요"], button[aria-label="like"], [class*="like-btn"], [class*="LikeButton"]'
      ))
      for (const btn of btns) {
        if (count >= TARGET) break
        const svg = btn.querySelector('svg')
        const liked = btn.getAttribute('aria-pressed') === 'true'
          || (btn.className || '').includes('liked')
          || (btn.className || '').includes('active')
          || (svg && (
            svg.querySelector('path[fill="#FF3B30"]')
            || svg.querySelector('path[fill="red"]')
            || svg.querySelector('path[fill="#ff3b5c"]')
            || svg.querySelector('use[href*="heart-fill"]')
          ))
        if (liked) continue
        const r = btn.getBoundingClientRect()
        if (r.bottom < 0 || r.top > window.innerHeight) {
          btn.scrollIntoView({ behavior: 'smooth', block: 'center' })
          await sleep(700)
        }
        btn.click()
        count++
        log(`좋아요 ${count}/${TARGET}`)
        await sleep(1500 + Math.random() * 800)
      }
      if (count < TARGET) {
        window.scrollBy({ top: 1200, behavior: 'smooth' })
        await sleep(1800)
      }
    }

    if (count > 0) {
      log(`좋아요 ${count}개 완료 — 미션 페이지 복귀`)
      // 미션 페이지 복귀 플래그
      await chrome.storage.local.set({ __sambaSnapLikesDone: true })
      // 미션 페이지로 이동
      location.href = 'https://www.musinsa.com/mission?tab=snap-daily-like'
    } else {
      send({ success: false, error: 'no_like_buttons' })
    }
    return
  }

  // 그 외 페이지 (예: 로그인) → 에러 보고
  send({ success: false, error: `unexpected page: ${location.pathname}` })
})()
