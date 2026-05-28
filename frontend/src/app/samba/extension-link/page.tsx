'use client'

import { useCallback, useEffect, useState } from 'react'
import { SAMBA_PREFIX, fetchWithAuth } from '@/lib/samba/legacy'
import { getDeviceId } from '@/lib/samba/deviceId'

export default function ExtensionLinkPage() {
  const [status, setStatus] = useState<'idle' | 'loading' | 'done' | 'error'>('loading')
  const [message, setMessage] = useState('')

  const issueKey = useCallback(async () => {
    setStatus('loading')
    setMessage('')
    try {
      // 확장앱 deviceId 동봉 — 백엔드가 키.device_id 컬럼에 저장해
      // 오토튠 status "본인 PC 매칭"에 사용 (누락 시 device_id=None 으로 매칭 불가).
      // race condition fix (2026-05-28): content script postMessage가 sessionStorage에
      // 도착하기 전에 issueKey가 발사되면 deviceId 빈 값으로 발급되어 device_id=NULL
      // row만 쌓임 → 오토튠 본인 PC 매칭 실패. 최대 3초 polling 후 발급.
      let deviceId = getDeviceId()
      if (!deviceId) {
        for (let i = 0; i < 30; i++) {
          await new Promise(r => setTimeout(r, 100))
          deviceId = getDeviceId()
          if (deviceId) break
        }
      }
      const headers: Record<string, string> = { 'Content-Type': 'application/json' }
      if (deviceId) headers['X-Device-Id'] = deviceId
      const res = await fetchWithAuth(`${SAMBA_PREFIX}/extension-keys`, {
        method: 'POST',
        headers,
        body: JSON.stringify({ label: `확장앱 ${new Date().toLocaleDateString('ko-KR')}` }),
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        // 401/403 = 미로그인 → 로그인 안내
        if (res.status === 401 || res.status === 403) {
          setMessage('로그인이 필요합니다. 아래 버튼으로 로그인 후 다시 시도하세요.')
        } else {
          setMessage(err.detail || `오류 (HTTP ${res.status})`)
        }
        setStatus('error')
        return
      }
      const data = await res.json()
      // 확장앱 content script 에 키 전달 (content-samba-deviceid.js 가 수신 → 저장 후 이 탭 자동 닫음)
      window.postMessage(
        { source: 'samba-page', type: 'SAMBA_SET_API_KEY', apiKey: data.key },
        window.location.origin,
      )
      setMessage('✅ 확장앱 연결 완료! 이 탭은 곧 자동으로 닫힙니다.')
      setStatus('done')
    } catch (e) {
      setMessage(e instanceof Error ? e.message : '알 수 없는 오류')
      setStatus('error')
    }
  }, [])

  // 페이지 진입 시 자동 발급 — 사용자가 버튼을 누를 필요 없음.
  // 로그인돼 있으면 즉시 키 발급 후 확장앱이 탭을 자동으로 닫는다.
  useEffect(() => {
    void issueKey()
  }, [issueKey])

  return (
    <div className="flex min-h-screen items-center justify-center bg-[#0F0F0F]">
      <div className="w-80 rounded-xl border border-[#2D2D2D] bg-[#1A1A1A] p-6 text-[#E5E5E5]">
        <h1 className="mb-1 text-lg font-bold text-[#FFB84D]">SAMBA-WAVE 확장앱 연결</h1>
        <p className="mb-5 text-xs text-[#888]">
          이 계정 전용 API 키를 발급해 확장앱에 자동 저장합니다.
        </p>

        {status === 'loading' && (
          <p className="text-center text-sm text-[#FFB84D]">🔑 자동 연결 중...</p>
        )}

        {status === 'error' && (
          <button
            onClick={() => void issueKey()}
            className="w-full rounded-md bg-[#FFB84D] py-2.5 text-sm font-bold text-black hover:bg-[#FFC870]"
          >
            🔄 다시 시도
          </button>
        )}

        {message && (
          <p className={`mt-3 text-center text-sm ${status === 'error' ? 'text-red-400' : 'text-green-400'}`}>
            {message}
          </p>
        )}

        {status === 'error' && (
          <p className="mt-4 text-center text-xs text-[#666]">
            미로그인 시{' '}
            <a href="/samba/login" className="text-[#FFB84D] underline">로그인</a>
            하세요.
          </p>
        )}
      </div>
    </div>
  )
}
