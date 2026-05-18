// content-reward-abcmart-attend.js
// ABC마트 출석체크 자동 수행 (kream-auto-review 포팅)
// 흐름: POST /rest/attend → overlapAttend Y(이미완료) / stampYN Y(스탬프획득) / 그외(정상완료)
;(async () => {
  if (window.__sambaRewardAbcAttendSent__) return
  window.__sambaRewardAbcAttendSent__ = true

  const BASE = 'https://member.a-rt.com'

  function send(extra) {
    return chrome.runtime.sendMessage({
      type: 'REWARD_RESULT',
      rewardAction: 'abcmart_attendance',
      ...extra,
    }).catch(() => {})
  }

  function log(msg) {
    console.log('[적립금-ABC마트출석]', msg)
  }

  await new Promise(r => setTimeout(r, 1500))
  log('ABC마트 출석체크 페이지 진입')

  try {
    const resp = await fetch(`${BASE}/rest/attend`, {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        Referer: 'https://member.a-rt.com/p/attendance-check',
      },
      body: JSON.stringify({}),
    })

    const text = await resp.text()
    let data = null
    try { data = JSON.parse(text) } catch (_) {}

    if (!resp.ok) {
      log(`출석 실패 ${resp.status}`)
      send({ success: false, error: `status ${resp.status}` })
      return
    }

    if (data?.code === 'LOGIN' || data?.resultCode === 'LOGIN') {
      log('로그인이 필요합니다')
      send({ success: false, error: '로그인 필요' })
      return
    }

    const result = data?.resultData
    if (result === undefined || result === null) {
      log(`응답 파싱 실패: ${text.substring(0, 100)}`)
      send({ success: false, error: '응답 파싱 실패' })
      return
    }

    if (result.overlapAttend === 'Y') {
      log('오늘 이미 출석 완료')
      send({
        success: true,
        alreadyDone: true,
        stampCount: result.stampCount ?? 0,
        stampScore: result.stampScore ?? 0,
      })
    } else if (result.stampYN === 'Y') {
      log(`✓ 출석 + 스탬프 획득 (스탬프 ${result.stampCount}개, 점수 ${result.stampScore})`)
      send({
        success: true,
        alreadyDone: false,
        stampCount: result.stampCount ?? 0,
        stampScore: result.stampScore ?? 0,
      })
    } else {
      log(`✓ 출석 완료 (스탬프 ${result.stampCount}개, 남은일 ${result.attendanceCnt ?? '?'})`)
      send({
        success: true,
        alreadyDone: false,
        stampCount: result.stampCount ?? 0,
        stampScore: result.stampScore ?? 0,
      })
    }
  } catch (e) {
    log(`오류: ${e.message}`)
    send({ success: false, error: e.message })
  }
})()
