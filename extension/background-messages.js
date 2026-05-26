// ==================== 메시지 리스너 ====================

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  // 키 발급 완료 → popup이 연 연결 페이지(extension-link) 탭 자동 닫기.
  // content-samba-deviceid.js 가 키 저장 직후 이 메시지를 보냄.
  if (msg.type === 'SAMBA_CLOSE_LINK_TAB') {
    const tabId = sender?.tab?.id
    if (tabId) {
      try { chrome.tabs.remove(tabId) } catch {}
    }
    return
  }

  // content_script(삼바 프론트엔드 페이지)가 deviceId를 요청할 때 응답
  if (msg.type === 'GET_DEVICE_ID') {
    ;(async () => {
      try {
        const id = await SambaBackgroundCore.getOrCreateDeviceId()
        sendResponse({ deviceId: id })
      } catch {
        sendResponse({ deviceId: '' })
      }
    })()
    return true
  }

  // 무신사 쿠키 동기화
  if (msg.type === 'SYNC_COOKIES') {
    getMusinsaCookies().then(async ({ cookies, cookieStr, isLoggedIn }) => {
      if (!isLoggedIn) {
        sendResponse({ success: false, message: '무신사 쿠키가 없습니다.' })
        return
      }
      try {
        const result = await sendCookiesToProxy(cookieStr)
        sendResponse({ success: true, cookieCount: cookies.length, ...result })
      } catch (e) {
        sendResponse({ success: false, message: `프록시 연결 실패: ${e.message}` })
      }
    })
    return true
  }

  // 전체 상태 조회
  if (msg.type === 'GET_STATUS') {
    getMusinsaCookies().then(async ({ cookies, isLoggedIn }) => {
      const alarm = await chrome.alarms.get('kreamPoll')
      sendResponse({
        musinsa: { isLoggedIn, cookieCount: cookies.length },
        kream: { isLoggedIn: !!kreamCookie, cookieCount: kreamCookie ? kreamCookie.split(';').length : 0 },
        polling: !!alarm,
        proxyUrl: PROXY_URL,
      })
    })
    return true
  }

  // 오토튠 이 PC 참여/탈퇴 — 시작 버튼 클릭 시 joined:true, 중지 시 false
  if (msg.type === 'AUTOTUNE_JOIN_LOCAL') {
    const joined = !!msg.joined
    if (typeof globalThis._setLocalAutotuneJoined === 'function') {
      // sourceSites: null=전체, [...]=지정 소싱처 (불필요한 소싱처 pre-login 차단)
      globalThis._setLocalAutotuneJoined(joined, msg.sourceSites ?? null)
    }
    sendResponse({ success: true })
    return false
  }

  // 백엔드 URL 변경
  if (msg.type === 'SET_PROXY_URL') {
    const url = (msg.url || '').trim().replace(/\/$/, '')
    if (url) {
      PROXY_URL = url
      chrome.storage.local.set({ proxyUrl: url })
      console.log(`[설정] 백엔드 URL 저장: ${PROXY_URL}`)
      sendResponse({ success: true })
    } else {
      sendResponse({ success: false })
    }
    return false
  }

  // KREAM 로그인 페이지 열기
  if (msg.type === 'KREAM_OPEN_LOGIN') {
    chrome.tabs.create({ url: 'https://kream.co.kr/login' })
    sendResponse({ success: true })
    return false
  }

  // 스마트스토어 셀러센터 굿서비스/패널티 점수 스크래핑
  if (msg.type === 'SCRAPE_STORE_SCORES') {
    ;(async () => {
      try {
        // 1. 셀러센터 탭 찾기
        const tabs = await chrome.tabs.query({ url: 'https://sell.smartstore.naver.com/*' })
        let tab = tabs[0]
        if (!tab) {
          // 셀러센터 탭이 없으면 새로 열기
          tab = await chrome.tabs.create({ url: 'https://sell.smartstore.naver.com/#/seller/good-service-score', active: true })
          // 페이지 로딩 대기
          await new Promise(r => setTimeout(r, 3000))
          sendResponse({ success: false, message: '셀러센터를 열었습니다. 로그인 후 다시 버튼을 눌러주세요.' })
          return
        }

        // 2. 굿서비스 페이지로 이동
        await chrome.tabs.update(tab.id, { url: 'https://sell.smartstore.naver.com/#/seller/good-service-score', active: true })
        await new Promise(r => setTimeout(r, 2500))

        // 3. 굿서비스 DOM 스크래핑
        const gsResult = await chrome.scripting.executeScript({
          target: { tabId: tab.id },
          func: () => {
            // 로그인 체크
            if (document.querySelector('.login_area') || document.body.innerText.includes('로그인')) {
              if (!document.querySelector('.snb_area') && !document.querySelector('.lnb_area')) {
                return { error: 'login_required' }
              }
            }
            // 점수 항목 추출 (텍스트 기반)
            const scores = {}
            const text = document.body.innerText
            // 항목별 점수 추출 패턴
            const items = document.querySelectorAll('[class*="score"], [class*="grade"], [class*="item"], [class*="metric"], tr, li')
            items.forEach(el => {
              const t = el.innerText.trim()
              // "항목명 N점" 또는 "항목명 N" 패턴
              const m = t.match(/^(.+?)\s+(\d+(?:\.\d+)?)\s*점?$/m)
              if (m && m[1].length < 20) {
                scores[m[1].trim()] = parseFloat(m[2])
              }
            })
            // 전체 점수 텍스트도 포함
            const totalMatch = text.match(/(\d+(?:\.\d+)?)\s*점/)
            return { scores, rawText: text.substring(0, 2000), totalMatch: totalMatch ? totalMatch[1] : null }
          }
        })

        const gsData = gsResult?.[0]?.result
        if (gsData?.error === 'login_required') {
          sendResponse({ success: false, message: '셀러센터에 로그인해주세요.' })
          return
        }

        // 4. 패널티 페이지로 이동
        await chrome.tabs.update(tab.id, { url: 'https://sell.smartstore.naver.com/#/seller/policy', active: true })
        await new Promise(r => setTimeout(r, 2500))

        // 5. 패널티 DOM 스크래핑
        const penResult = await chrome.scripting.executeScript({
          target: { tabId: tab.id },
          func: () => {
            const text = document.body.innerText
            // 패널티 점수/비율 추출
            const penaltyMatch = text.match(/(\d+(?:\.\d+)?)\s*점/)
            const rateMatch = text.match(/(\d+(?:\.\d+)?)\s*%/)
            return { rawText: text.substring(0, 2000), penalty: penaltyMatch ? penaltyMatch[1] : null, penaltyRate: rateMatch ? rateMatch[1] : null }
          }
        })

        const penData = penResult?.[0]?.result

        // 6. 서버로 전송
        const accountId = msg.account_id || ''
        const payload = {
          account_id: accountId,
          good_service: gsData?.scores || null,
          penalty: penData?.penalty ? parseFloat(penData.penalty) : null,
          penalty_rate: penData?.penaltyRate ? parseFloat(penData.penaltyRate) : null,
        }

        const resp = await apiFetch(`${PROXY_URL}/api/v1/samba/monitor/store-scores/update`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        })
        const result = await resp.json()

        sendResponse({
          success: true,
          message: '스토어 점수 수집 완료',
          good_service: gsData?.scores,
          penalty: penData?.penalty,
          penalty_rate: penData?.penaltyRate,
        })
      } catch (e) {
        console.error('[스토어점수] 스크래핑 실패:', e)
        sendResponse({ success: false, message: `스크래핑 실패: ${e.message}` })
      }
    })()
    return true
  }

  // 가격/재고 갱신 요청 (프론트에서 확장앱 경유 필요 상품 전달)
  if (msg.type === 'REFRESH_PRODUCTS') {
    const productIds = msg.product_ids || []
    console.log(`[갱신] ${productIds.length}건 갱신 요청 수신`)
    // 현재는 서버에 다시 전달 (KREAM 등 인증 필요 사이트 수집 트리거)
    apiFetch(`${PROXY_URL}/api/v1/samba/collector/products/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ product_ids: productIds, auto_retransmit: true }),
    })
      .then(r => r.json())
      .then(data => {
        console.log(`[갱신] 결과:`, data)
        sendResponse({ success: true, ...data })
      })
      .catch(e => {
        console.error(`[갱신] 실패:`, e)
        sendResponse({ success: false, message: e.message })
      })
    return true
  }
})

// ==================== ABCmart / GrandStage 잔액 전송 ====================

async function sendAbcmartBalance(data) {
  try {
    const res = await apiFetch(`${PROXY_URL}/api/v1/samba/sourcing-accounts/sync-balance`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...data, site: data.siteName }),
    })
    if (res.ok) {
      const result = await res.json()
      console.log(`[잔액] ${data.siteName} 서버 저장 완료:`, result)
    } else {
      console.warn(`[잔액] ${data.siteName} 서버 저장 실패: HTTP ${res.status}`)
    }
  } catch (e) {
    console.log(`[잔액] ${data.siteName} 서버 전송 실패 (무시): ${e.message}`)
  }
}

// ==================== ABCmart 멤버십 등급 동기화 ====================

async function syncAbcmartMembership(rate, grade) {
  // 호환성 stub — 등급만 전송 (쿠키 없이)
  return handleAbcmartMembershipSync({ rate, grade, needsCookie: false, expired: false })
}

async function handleAbcmartMembershipSync({ rate, grade, needsCookie, expired }) {
  try {
    let cookieStr = ''
    if (needsCookie && !expired) {
      // .a-rt.com 도메인 모든 쿠키 추출 → 'k=v; k=v' 형식 직렬화
      try {
        const cookies = await chrome.cookies.getAll({ domain: 'a-rt.com' })
        cookieStr = cookies.map(c => `${c.name}=${c.value}`).join('; ')
        console.log(`[ABCmart] 쿠키 ${cookies.length}개 추출 (${cookieStr.length} bytes)`)
      } catch (e) {
        console.log(`[ABCmart] 쿠키 추출 실패: ${e.message}`)
      }
    }

    const payload = {
      site_name: 'ABCmart',
      membership_rate: rate,
      membership_grade: grade,
      expired: !!expired,
    }
    if (cookieStr) payload.cookie = cookieStr

    const res = await apiFetch(`${PROXY_URL}/api/v1/samba/sourcing-accounts/sync-membership`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    if (res.ok) {
      console.log(`[ABCmart] 서버 저장 완료: ${grade} (${rate}%) cookie=${cookieStr ? 'sent' : 'no'} expired=${expired}`)
      if (cookieStr) {
        chrome.storage.local.set({ abcmart_cookie_synced_at: Date.now() })
      }
    } else {
      console.warn(`[ABCmart] 서버 저장 실패: HTTP ${res.status}`)
    }
  } catch (e) {
    console.log(`[ABCmart] 서버 전송 실패 (무시): ${e.message}`)
  }
}

// ==================== SSG 판매자등급/평점 스크래핑 ====================

async function scrapeSSGScores() {
  const SSG_HOME_URL = 'https://po.ssgadm.com/main.ssg'
  const result = {}

  // po.ssgadm.com 탭 검색
  const allSsgTabs = await chrome.tabs.query({ url: 'https://po.ssgadm.com/*' })
  const homeTabs = allSsgTabs.filter(t => t.url && t.url.includes('main.ssg'))
  console.log(`[SSG스코어] po.ssgadm.com 탭 수: ${allSsgTabs.length}, 홈화면 탭 수: ${homeTabs.length}`)

  let tabId
  if (homeTabs.length > 0) {
    tabId = homeTabs[0].id
  } else if (allSsgTabs.length > 0) {
    tabId = allSsgTabs[0].id
    await chrome.tabs.update(tabId, { url: SSG_HOME_URL, active: true })
    await new Promise(r => setTimeout(r, 5000))
  } else {
    const tab = await chrome.tabs.create({ url: SSG_HOME_URL, active: false })
    tabId = tab.id
    await new Promise(r => setTimeout(r, 7000))
  }

  try {
    const [res] = await chrome.scripting.executeScript({
      target: { tabId },
      func: () => {
        const result = {}
        const parseGradeVal = (text) => {
          const m = text.match(/([\d.]+)\s*[%점]/)
          return m ? m[1] : text.trim()
        }
        // 테이블 행 순회 파싱
        const rows = document.querySelectorAll('tr')
        for (const row of rows) {
          const cells = row.querySelectorAll('td, th')
          if (cells.length >= 2) {
            const label = (cells[0].textContent || '').trim()
            const value = (cells[1].textContent || '').trim()
            if (label.includes('서비스평점') || label.includes('서비스 평점')) {
              const g = value.match(/([\d.]+)/)
              if (!result.ssg_service_score) result.ssg_service_score = g ? g[1] : value
            } else if (label.includes('주문이행')) {
              if (!result.ssg_order_fulfill) result.ssg_order_fulfill = parseGradeVal(value)
            } else if (label.includes('출고준수')) {
              if (!result.ssg_ship_comply) result.ssg_ship_comply = parseGradeVal(value)
            } else if (label.includes('24시간') || label.includes('답변')) {
              if (!result.ssg_reply_rate) result.ssg_reply_rate = parseGradeVal(value)
            } else if (label.includes('판매등급') || label.includes('판매자등급')) {
              const g = value.match(/([A-Z가-힣]+)/)
              if (!result.ssg_seller_grade) result.ssg_seller_grade = g ? g[1] : value
            }
          }
        }
        // fallback: 전체 텍스트 정규식
        if (!result.ssg_service_score) {
          const fullText = document.body.innerText || ''
          const patterns = {
            ssg_service_score: /서비스\s*평점[:\s]*([\d.]+)/,
            ssg_order_fulfill: /주문이행[:\s]*([\d.]+)/,
            ssg_ship_comply: /출고준수[:\s]*([\d.]+)/,
            ssg_reply_rate: /(?:24시간|답변)[:\s]*([\d.]+)/,
            ssg_seller_grade: /판매(?:자)?등급[:\s]*([A-Z가-힣]+)/,
          }
          for (const [key, re] of Object.entries(patterns)) {
            if (!result[key]) {
              const m = fullText.match(re)
              if (m) result[key] = m[1]
            }
          }
        }
        return result
      },
    })
    Object.assign(result, res.result || {})
  } catch (e) {
    console.error(`[SSG스코어] 스크래핑 실패:`, e)
  }

  console.log(`[SSG스코어] 결과:`, result)
  return result
}
