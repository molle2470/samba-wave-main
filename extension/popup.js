// ============================================================
// SAMBA-WAVE 팝업 UI 로직
// - 백엔드 URL 저장 → 키 없으면 연결 페이지 자동 오픈(자동 발급)
// - 연결 테스트 (/api/v1/health)
// ============================================================

// 키 발급용 연결 페이지 — 저장 시 키가 없으면 자동으로 이 페이지를 열어
// 로그인 세션 기반으로 키를 발급받고 탭이 자동으로 닫힌다.
// 포크 환경 대응(이슈 #257): 저장된 백엔드 URL 에서 frontend 도메인을
// 동적으로 유도. api.* → 루트로 치환. proxyUrl 미설정 시만 본진 fallback.
const DEFAULT_EXTENSION_LINK = 'https://samba-wave.vercel.app/samba/extension-link'

function deriveExtensionLinkUrl(proxyUrl) {
  if (!proxyUrl) return DEFAULT_EXTENSION_LINK
  const frontendBase = proxyUrl.replace(/^https?:\/\/api\./i, (m) => m.replace('api.', ''))
  return `${frontendBase}/samba/extension-link`
}

function $(id) { return document.getElementById(id) }

function setStatus(el, msg, type = '') {
  el.textContent = msg
  el.className = 'status' + (type ? ' ' + type : '')
}

function normalizeUrl(raw) {
  if (!raw) return ''
  let v = raw.trim().replace(/\/+$/, '')
  if (v && !/^https?:\/\//i.test(v)) {
    v = 'https://' + v
  }
  return v
}

// ============================================================
// 초기화
// ============================================================
document.addEventListener('DOMContentLoaded', async () => {
  $('version').textContent = `v${chrome.runtime.getManifest().version}`

  const status = $('status')
  const urlInput = $('proxyUrl')

  // 저장된 값 로드
  const saved = await chrome.storage.local.get(['proxyUrl'])
  urlInput.value = saved.proxyUrl || ''

  if (!saved.proxyUrl) {
    setStatus(status, '⚠️ 최초 1회 백엔드 URL을 입력하세요', 'err')
  }

  // 연결 상태 뱃지 갱신
  function updateConn(connected) {
    const dot = $('connDot')
    const txt = $('connText')
    if (connected) {
      txt.textContent = '연결됨'
      dot.style.background = '#22c55e'
    } else {
      txt.textContent = '미연결 — 저장 시 자동 연결'
      dot.style.background = '#FF6B6B'
    }
  }

  // ============================================================
  // 저장 → 키 없으면 연결 페이지 자동 오픈(자동 발급)
  // ============================================================
  $('btnSave').addEventListener('click', async () => {
    const url = normalizeUrl(urlInput.value)

    if (!url) {
      setStatus(status, '❌ 백엔드 URL은 필수입니다', 'err')
      return
    }

    const prev = await chrome.storage.local.get(['proxyUrl', 'apiKey'])
    await chrome.storage.local.set({ proxyUrl: url })
    urlInput.value = url

    // URL 동일 + 키 있음 → 기존 연결 유지 (재로그인 불필요)
    if (prev.proxyUrl === url && prev.apiKey) {
      setStatus(status, '✅ 저장됨', 'ok')
      updateConn(true)
      return
    }

    // URL 변경 또는 키 없음 → 연결 페이지 자동 오픈.
    // 로그인돼 있으면 즉시 키 발급 후 탭 자동 닫힘, 미로그인이면 로그인 안내.
    await chrome.storage.local.remove('apiKey')
    setStatus(status, '✅ 저장됨 — 자동 연결 중...', 'ok')
    updateConn(false)
    chrome.tabs.create({ url: deriveExtensionLinkUrl(url), active: true })
  })

  // ============================================================
  // 연결 테스트
  // ============================================================
  $('btnTest').addEventListener('click', async () => {
    const url = normalizeUrl(urlInput.value)
    if (!url) {
      setStatus(status, '❌ URL을 먼저 입력하세요', 'err')
      return
    }

    setStatus(status, '⏳ 연결 확인 중...', '')
    const btn = $('btnTest')
    btn.disabled = true
    try {
      const ctrl = new AbortController()
      const timer = setTimeout(() => ctrl.abort(), 8000)
      const res = await fetch(`${url}/api/v1/health`, {
        method: 'GET',
        signal: ctrl.signal,
      })
      clearTimeout(timer)
      if (res.ok) {
        const j = await res.json().catch(() => ({}))
        const w = j.worker?.alive ? ' (worker alive)' : ''
        setStatus(status, `✅ 연결 성공 — HTTP ${res.status}${w}`, 'ok')
      } else {
        setStatus(status, `❌ HTTP ${res.status} — URL 또는 인증 키 확인`, 'err')
      }
    } catch (e) {
      const msg = e.name === 'AbortError' ? '타임아웃 (8초)' : e.message
      setStatus(status, `❌ 연결 실패 — ${msg}`, 'err')
    } finally {
      btn.disabled = false
    }
  })

  // 현재 저장된 키 유무로 연결 상태 표시
  const keyData = await chrome.storage.local.get(['apiKey'])
  updateConn(!!keyData.apiKey)
})
