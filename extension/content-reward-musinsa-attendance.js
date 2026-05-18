// content-reward-musinsa-attendance.js
// 무신사 출석체크 자동 수행 (kream-auto-review 포팅)
// 흐름:
//  1) GET /onboarding/api/v1/attendance-book/platform → isCompletedToday 확인
//  2) 이미 완료 → REWARD_RESULT(alreadyDone:true)
//  3) 미완료 → POST .../perform → REWARD_RESULT(reward, streakCount)
;(async () => {
  if (window.__sambaRewardAttendanceSent__) return
  window.__sambaRewardAttendanceSent__ = true

  const BASE = 'https://onboarding.musinsa.com'

  function send(extra) {
    return chrome.runtime.sendMessage({
      type: 'REWARD_RESULT',
      rewardAction: 'musinsa_attendance',
      ...extra,
    }).catch(() => {})
  }

  function log(msg) {
    console.log('[적립금-무신사출석]', msg)
  }

  await new Promise(r => setTimeout(r, 1500))
  log('출석체크 페이지 진입 — 상태 확인 중')

  try {
    // 로그인 페이지 리다이렉트 감지
    if (location.href.includes('login') || location.href.includes('member.one.musinsa')) {
      log('로그인 페이지 감지 — 쿠키 만료')
      send({ success: false, error: '로그인 필요' })
      return
    }

    const statusResp = await fetch(`${BASE}/onboarding/api/v1/attendance-book/platform`, {
      credentials: 'include',
      headers: { Accept: 'application/json', Referer: 'https://www.musinsa.com/events/attendance' },
    })

    if (!statusResp.ok) {
      log(`상태 조회 실패 ${statusResp.status}`)
      send({ success: false, error: `status ${statusResp.status}` })
      return
    }

    const statusData = await statusResp.json()
    const d = statusData?.data || {}
    const todayStr = new Date().toISOString().slice(0, 10).replace(/-/g, '')
    const attendanceDates = d.attendanceDates || []
    const alreadyDone = d.isCompletedToday === true ||
      attendanceDates.some(a => a.date && a.date.startsWith(todayStr))

    if (alreadyDone) {
      const todayEntry = attendanceDates.find(a => a.date && a.date.startsWith(todayStr))
      log(`이미 출석 완료 (적립금 ${todayEntry?.rewardValue ?? '?'}원)`)
      send({
        success: true,
        alreadyDone: true,
        reward: todayEntry?.rewardValue ?? 0,
        streakCount: d.currentStreakCount ?? 0,
      })
      return
    }

    log('출석체크 수행 중')
    const performResp = await fetch(`${BASE}/onboarding/api/v1/attendance-book/platform/perform`, {
      method: 'POST',
      credentials: 'include',
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
        Referer: 'https://www.musinsa.com/events/attendance',
      },
      body: JSON.stringify({}),
    })

    const performText = await performResp.text()
    let performData = null
    try { performData = JSON.parse(performText) } catch (_) {}

    if (performResp.ok && performData?.data?.success) {
      const pd = performData.data
      log(`✓ 출석체크 완료 적립금 ${pd.rewardValue}원 (${pd.streakCount}일 연속)`)
      send({
        success: true,
        alreadyDone: false,
        reward: pd.rewardValue ?? 0,
        streakCount: pd.streakCount ?? 0,
      })
    } else if (performResp.status === 409 || performData?.meta?.errorCode) {
      log('이미 출석 완료 (중복 응답)')
      send({ success: true, alreadyDone: true, reward: 0, streakCount: 0 })
    } else {
      log(`출석 실패 ${performResp.status}: ${performText.substring(0, 100)}`)
      send({ success: false, error: `${performResp.status}: ${performText.substring(0, 80)}` })
    }
  } catch (e) {
    log(`오류: ${e.message}`)
    send({ success: false, error: e.message })
  }
})()
