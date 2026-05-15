/**
 * content-musinsa-orderlist.js
 *
 * 무신사 마이페이지 주문 목록 페이지에서 ord_no + ord_opt_no 매핑 추출.
 *
 * 동작:
 *   - 페이지 DOM 안 "배송조회" 링크들의 href에서 ord_no/ord_opt_no 파싱
 *   - background로 한 번에 전송 → 백엔드 POST /musinsa/save-opt-nos
 *   - DOM 변경 감지(MutationObserver)로 페이지네이션/탭전환 후 재스캔
 *
 * 이 매핑이 있어야 백엔드가 musinsa deliveryInfo API 직접 호출 가능.
 * 확장앱 탭 폴링 없이 송장 자동 수집됨.
 */
;(() => {
  'use strict'

  let lastSentKeySet = ''
  let scanTimer = null

  function extractMappings() {
    const mappings = []
    const seen = new Set()
    // "배송조회" 링크들 — href에 trace?ord_no=...&ord_opt_no=... 포함
    const links = document.querySelectorAll(
      'a[href*="/order-service/my/delivery/trace"]'
    )
    for (const a of links) {
      try {
        const href = a.getAttribute('href') || ''
        const u = new URL(href, location.origin)
        const ord_no = u.searchParams.get('ord_no')
        const ord_opt_no = u.searchParams.get('ord_opt_no')
        if (!ord_no || !ord_opt_no) continue
        const key = `${ord_no}|${ord_opt_no}`
        if (seen.has(key)) continue
        seen.add(key)
        mappings.push({ ord_no, ord_opt_no })
      } catch {}
    }
    return mappings
  }

  async function sendIfNew() {
    const mappings = extractMappings()
    if (mappings.length === 0) return
    const keys = mappings.map(m => `${m.ord_no}|${m.ord_opt_no}`).sort().join(',')
    if (keys === lastSentKeySet) return // 변경 없음
    lastSentKeySet = keys
    try {
      const resp = await chrome.runtime.sendMessage({
        type: 'MUSINSA_SAVE_OPT_NOS',
        mappings,
      })
      if (resp?.ok) {
        console.log(
          `[무신사 옵션번호] ${resp.updated}건 저장 (받은 ${resp.received}, 미매칭 ${resp.notMatched})`
        )
      }
    } catch (e) {
      console.warn('[무신사 옵션번호] background 전송 실패:', e?.message || e)
    }
  }

  function scheduleScan() {
    if (scanTimer) clearTimeout(scanTimer)
    scanTimer = setTimeout(sendIfNew, 800)
  }

  // 초기 스캔 + DOM 변경 감지
  scheduleScan()
  const obs = new MutationObserver(scheduleScan)
  obs.observe(document.body, { childList: true, subtree: true })
})()
