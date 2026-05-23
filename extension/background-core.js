(function () {
  // CLOUD_URL 은 extension-web/config.js 에서 주입. 누락 시 기본값 폴백.
  const _CFG_URL =
    (typeof self !== 'undefined' && self.SAMBA_CONFIG && self.SAMBA_CONFIG.CLOUD_URL) ||
    ''
  const DEFAULT_PROXY_URL = _CFG_URL
  const CLOUD_URL = _CFG_URL
  const API_PREFIX = '/api/v1/samba/proxy'
  const DEFAULT_SELECTORS = {
    kream_size_items: '.select_item',
    kream_bottom_sheet: '.layer_bottom_sheet--open',
    kream_buy_button_text: '구매하기',
    kream_fast_delivery: '빠른배송',
    kream_normal_delivery: '일반배송',
  }

  // 이 브라우저(확장앱 인스턴스)의 고유 deviceId를 발급·캐시한다.
  // 백엔드 collect-queue 폴링에 X-Device-Id 헤더로 첨부되어,
  // 오토튠이 발행한 작업을 "오토튠을 시작한 브라우저"만 받아가게 한다.
  // 크롬 계정 동기화로 다른 PC에 확장앱이 설치되어도, 그 PC는 다른 deviceId를 가지므로
  // 집/사무실 간 중복 탭 오픈이 발생하지 않는다.
  let _cachedDeviceId = ''
  async function getOrCreateDeviceId() {
    if (_cachedDeviceId) return _cachedDeviceId
    try {
      const cached = await chrome.storage.local.get('deviceId')
      if (cached.deviceId) {
        _cachedDeviceId = cached.deviceId
        return _cachedDeviceId
      }
      const fresh = (globalThis.crypto?.randomUUID?.() || '')
        || ('dev-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10))
      await chrome.storage.local.set({ deviceId: fresh })
      _cachedDeviceId = fresh
      return fresh
    } catch {
      return ''
    }
  }

  async function loadApiKey() {
    // 테넌트 키 전용 — storage 에 저장된 키만 사용한다.
    // 키는 삼바 웹페이지(/samba/extension-link)에서 로그인 후 발급받아
    // SAMBA_SET_API_KEY 메시지로 주입된다. 글로벌 키 자동발급 경로는 제거됨
    // (글로벌 키는 tenant 격리가 안 되고 owner-guard 로 막혀 무한 403 유발).
    const cached = await chrome.storage.local.get('apiKey')
    return cached.apiKey || ''
  }

  async function apiFetch(url, init = {}) {
    const proxyData = await chrome.storage.local.get(['proxyUrl', 'allowedSites'])
    const apiKey = await loadApiKey()
    const deviceId = await getOrCreateDeviceId()
    // 사이트 필터 (PC 분담) — chrome.storage.allowedSites:
    //   - undefined/null = 미설정(전체 처리, 디폴트) → 헤더 미부착
    //   - 배열 (빈 배열 포함) = 명시적 설정. 빈 배열 = "이 PC는 아무 작업도 안 받음"
    //   - 헤더 'X-Allowed-Sites'는 값이 빈 문자열이어도 부착되어 백엔드가 명시적 0개로 인식
    const allowedSites = Array.isArray(proxyData.allowedSites) ? proxyData.allowedSites : null
    // 확장앱 버전 — 백엔드가 reward 등 신기능 잡을 옛 PC에 안 주도록 필터링용
    const extVersion = chrome.runtime.getManifest().version
    const headers = {
      ...(init.headers || {}),
      'X-Api-Key': apiKey,
      'X-Device-Id': deviceId,
      'X-Ext-Version': extVersion,
    }
    if (allowedSites !== null) {
      headers['X-Allowed-Sites'] = allowedSites.join(',')
    }
    const res = await fetch(url, { ...init, headers })
    if (res.status === 403) {
      // 키가 무효/만료/revoke 됨. 글로벌 키 재발급 시도는 제거 —
      // 글로벌 발급은 owner-guard 로 막혀 무한 403 루프를 만들었다.
      // 사용자가 /samba/extension-link 에서 로그인 후 키를 재발급하면
      // SAMBA_SET_API_KEY 메시지로 storage 가 갱신된다.
      if (!apiKey) {
        console.warn('[SAMBA] API 키 없음 — 삼바 로그인 후 /samba/extension-link 에서 키 발급 필요')
      } else {
        console.warn('[SAMBA] API 키 거부(403) — 키 만료/revoke 가능. /samba/extension-link 에서 재발급 필요')
      }
    }
    return res
  }

  async function loadSelectors(proxyUrl) {
    try {
      const res = await apiFetch(`${proxyUrl}${API_PREFIX}/extension-config`)
      const config = res.ok ? await res.json() : null
      if (config?.selectors) {
        return { ...DEFAULT_SELECTORS, ...config.selectors }
      }
    } catch {
      // ignore and use defaults
    }
    return { ...DEFAULT_SELECTORS }
  }

  async function sendSiteCookieToProxy({ proxyUrl, site, cookieStr }) {
    // (2026-05-20) CLOUD_URL 미러 전송 영구 제거 — 포크 유저 쿠키가 원본
    // 백엔드로 자동 미러되던 누수 사고 차단. proxyUrl(본인 백엔드)에만 전송.
    const endpoint = `${API_PREFIX}/${site}/set-cookie`
    const res = await apiFetch(`${proxyUrl}${endpoint}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cookie: cookieStr }),
    })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    return res.json()
  }

  // 버전 비교 — semver 'major.minor.patch' 가정, 짧으면 0으로 패딩.
  // 백엔드 collect-queue 가 minExtVersion 을 응답하면 background-sourcing.js 에서 활용.
  function _isExtVersionBelow(minVersion) {
    try {
      const cur = (chrome?.runtime?.getManifest?.()?.version || '').split('.').map(n => parseInt(n) || 0)
      const min = String(minVersion).split('.').map(n => parseInt(n) || 0)
      const len = Math.max(cur.length, min.length)
      for (let i = 0; i < len; i++) {
        const a = cur[i] || 0
        const b = min[i] || 0
        if (a < b) return true
        if (a > b) return false
      }
      return false
    } catch {
      return false
    }
  }
  globalThis._isExtVersionBelow = _isExtVersionBelow

  globalThis.SambaBackgroundCore = {
    API_PREFIX,
    CLOUD_URL,
    DEFAULT_PROXY_URL,
    DEFAULT_SELECTORS,
    apiFetch,
    loadSelectors,
    sendSiteCookieToProxy,
    getOrCreateDeviceId,
  }
})()
