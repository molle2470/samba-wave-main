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

  async function loadApiKey(proxyUrl) {
    const cached = await chrome.storage.local.get('apiKey')
    if (cached.apiKey) return cached.apiKey

    try {
      const url = proxyUrl || DEFAULT_PROXY_URL
      const res = await fetch(`${url}/api/v1/samba/sourcing-accounts/extension-key`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      })
      if (res.ok) {
        const data = await res.json()
        await chrome.storage.local.set({ apiKey: data.api_key })
        return data.api_key
      }
    } catch {}
    return ''
  }

  async function apiFetch(url, init = {}) {
    const proxyData = await chrome.storage.local.get(['proxyUrl', 'allowedSites'])
    const apiKey = await loadApiKey(proxyData.proxyUrl)
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
      await chrome.storage.local.remove('apiKey')
      // 실제 요청 URL의 origin으로 키 재발급 (storage의 proxyUrl이 localhost일 수 있음)
      const serverBase = new URL(url).origin
      const newKey = await loadApiKey(serverBase)
      const retryHeaders = {
        ...(init.headers || {}),
        'X-Api-Key': newKey,
        'X-Device-Id': deviceId,
        'X-Ext-Version': extVersion,
      }
      if (allowedSites !== null) {
        retryHeaders['X-Allowed-Sites'] = allowedSites.join(',')
      }
      return fetch(url, { ...init, headers: retryHeaders })
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
    const endpoint = `${API_PREFIX}/${site}/set-cookie`

    if (proxyUrl !== CLOUD_URL) {
      try {
        await apiFetch(`${CLOUD_URL}${endpoint}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ cookie: cookieStr }),
        })
      } catch {
        // ignore cloud mirror failures
      }
    }

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
