'use client'

import { useState, useEffect } from 'react'
import { card, inputStyle, fmtNum } from '@/lib/samba/styles'
import {
  sourcingAccountApi,
  type SambaSourcingAccount,
  type ChromeProfile,
} from '@/lib/samba/api/operations'
import { showAlert } from '@/components/samba/Modal'
import type { SourcingAccountsState, SourcingAccountsActions } from '../hooks/useSourcingAccounts'

type Props = SourcingAccountsState & Pick<SourcingAccountsActions,
  'handleSyncChromeProfiles' | 'handleSourcingSave' | 'handleSourcingDelete' |
  'handleSourcingEdit' | 'handleFetchBalance' | 'handleFetchAllBalances' |
  'setSourcingTab' | 'setSourcingEditId' | 'setSourcingForm'
> & {
  loadSourcingAccounts: () => Promise<void>
}

export function SourcingAccountsPanel(props: Props) {
  const {
    sourcingAccounts,
    sourcingSites,
    chromeProfiles,
    chromeProfilesSyncing,
    sourcingTab,
    sourcingEditId,
    sourcingForm,
    balanceLoading,
    normalizedChromeProfiles,
    handleSyncChromeProfiles,
    handleSourcingSave,
    handleSourcingDelete,
    handleSourcingEdit,
    handleFetchBalance,
    handleFetchAllBalances,
    setSourcingTab,
    setSourcingEditId,
    setSourcingForm,
    loadSourcingAccounts,
  } = props

  // 비밀번호 보기 토글 — 수정 모드에서 마스킹값이면 reveal API 호출해서 진짜 비번 fetch
  const [showPassword, setShowPassword] = useState(false)
  const [revealing, setRevealing] = useState(false)

  // 다른 계정으로 수정 전환 또는 폼 리셋 시 항상 마스킹 상태로 복귀 (값 노출 방지)
  useEffect(() => {
    setShowPassword(false)
  }, [sourcingEditId])

  // 마스킹 패턴 감지 — backend masking.py의 _MASKED_PATTERN과 동일 (^\*{4}.{0,4}$)
  const _isMasked = (v: string) => /^\*{4}.{0,4}$/.test(v || '')

  const handleTogglePassword = async () => {
    // 이미 보이는 상태면 단순 숨김 (값은 유지)
    if (showPassword) {
      setShowPassword(false)
      return
    }
    // 폼 password가 마스킹값이면 reveal API로 진짜 password fetch (수정 모드만)
    if (sourcingEditId && _isMasked(sourcingForm.password)) {
      setRevealing(true)
      try {
        const res = await sourcingAccountApi.revealPassword(sourcingEditId)
        setSourcingForm(prev => ({ ...prev, password: res.password }))
        setShowPassword(true)
      } catch (err) {
        showAlert(err instanceof Error ? err.message : '비밀번호 조회 실패', 'error')
      } finally {
        setRevealing(false)
      }
      return
    }
    // 마스킹 아님 → 그대로 표시
    setShowPassword(true)
  }

  return (
    <div style={{ ...card, padding: '1.5rem', marginTop: '1.5rem' }}>
      <div style={{ fontSize: '1rem', fontWeight: 700, color: '#E5E5E5', marginBottom: '0.25rem' }}>소싱처 계정</div>
      <p style={{ fontSize: '0.8125rem', color: '#666', marginBottom: '1.25rem' }}>소싱처별 로그인 계정을 관리합니다</p>

      {/* 소싱처 탭바 */}
      <div style={{ borderBottom: '1px solid #2D2D2D', marginBottom: '1.5rem' }}>
        <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 0 }}>
          {sourcingSites.map(site => {
            const count = sourcingAccounts.filter(a => a.site_name === site.id).length
            return (
              <button
                key={site.id}
                onClick={() => {
                  setSourcingTab(site.id)
                  setSourcingEditId(null)
                  setSourcingForm({ site_name: site.id, account_label: '', username: '', password: '', chrome_profile: '', memo: '' })
                }}
                style={{
                  padding: '0.5rem 0.75rem', background: 'none', border: 'none',
                  borderBottom: sourcingTab === site.id ? '2px solid #FF8C00' : '2px solid transparent',
                  color: sourcingTab === site.id ? '#FF8C00' : '#666',
                  fontSize: '0.8125rem', fontWeight: sourcingTab === site.id ? 600 : 400,
                  cursor: 'pointer', marginBottom: '-1px', whiteSpace: 'nowrap',
                }}
              >{site.name}{count > 0 ? ` (${fmtNum(count)})` : ''}</button>
            )
          })}
        </div>
      </div>

      {/* 좌측: 인라인 폼 + 우측: 계정 리스트 */}
      <div style={{ display: 'flex', gap: '2rem', alignItems: 'flex-start' }}>
        {/* 좌측: 입력 폼 */}
        <div style={{ flex: 1, maxWidth: '560px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '1rem' }}>
            <span style={{ fontSize: '0.9375rem', fontWeight: 600, color: '#E5E5E5' }}>
              {sourcingSites.find(s => s.id === sourcingTab)?.name || sourcingTab} 계정
            </span>
            {sourcingEditId && (
              <>
                <span style={{ fontSize: '0.75rem', color: '#FF8C00', fontWeight: 600 }}>
                  ({sourcingAccounts.find(a => a.id === sourcingEditId)?.account_label} 수정중)
                </span>
                <button
                  onClick={() => {
                    setSourcingEditId(null)
                    setSourcingForm({ site_name: sourcingTab, account_label: '', username: '', password: '', chrome_profile: '', memo: '' })
                  }}
                  style={{ padding: '0.2rem 0.5rem', fontSize: '0.7rem', background: 'rgba(255,80,80,0.1)', border: '1px solid rgba(255,80,80,0.3)', borderRadius: '4px', color: '#FF6B6B', cursor: 'pointer' }}
                >취소</button>
              </>
            )}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
              <label style={{ color: '#888', fontSize: '0.875rem', minWidth: '120px', flexShrink: 0 }}>별칭</label>
              <input style={{ ...inputStyle, flex: 1 }} placeholder="별칭" value={sourcingForm.account_label} onChange={e => setSourcingForm(prev => ({ ...prev, account_label: e.target.value }))} />
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
              <label style={{ color: '#888', fontSize: '0.875rem', minWidth: '120px', flexShrink: 0 }}>아이디</label>
              <input style={{ ...inputStyle, flex: 1 }} placeholder="로그인 아이디" value={sourcingForm.username} onChange={e => setSourcingForm(prev => ({ ...prev, username: e.target.value }))} />
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
              <label style={{ color: '#888', fontSize: '0.875rem', minWidth: '120px', flexShrink: 0 }}>비밀번호</label>
              <input
                style={{ ...inputStyle, flex: 1 }}
                type={showPassword ? 'text' : 'password'}
                placeholder="로그인 비밀번호"
                value={sourcingForm.password}
                onChange={e => {
                  setSourcingForm(prev => ({ ...prev, password: e.target.value }))
                  // 사용자가 직접 수정 시작하면 평문 그대로 보이게 유지
                  if (!showPassword) setShowPassword(true)
                }}
              />
              <button
                type="button"
                onClick={handleTogglePassword}
                disabled={revealing}
                title={showPassword ? '숨기기' : '보기 (저장된 비밀번호 확인)'}
                style={{
                  padding: '0.55rem 0.8rem',
                  background: showPassword ? 'rgba(255,140,0,0.15)' : 'rgba(76,154,255,0.12)',
                  color: revealing ? '#666' : (showPassword ? '#FF8C00' : '#4C9AFF'),
                  border: `1px solid ${showPassword ? 'rgba(255,140,0,0.3)' : 'rgba(76,154,255,0.3)'}`,
                  borderRadius: '6px',
                  fontSize: '0.8125rem',
                  cursor: revealing ? 'not-allowed' : 'pointer',
                  whiteSpace: 'nowrap',
                  flexShrink: 0,
                }}
              >{revealing ? '조회중...' : (showPassword ? '숨기기' : '보기')}</button>
            </div>
            {sourcingTab === 'MUSINSA' && (
              <>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                  <label style={{ color: '#888', fontSize: '0.875rem', minWidth: '120px', flexShrink: 0 }}>크롬 프로필</label>
                  <select style={{ ...inputStyle, flex: 1 }} value={sourcingForm.chrome_profile} onChange={e => setSourcingForm(prev => ({ ...prev, chrome_profile: e.target.value }))}>
                    <option value="">선택 안함</option>
                    {normalizedChromeProfiles.map((p, idx) => <option key={`${p.email || p.directory || 'profile'}-${idx}`} value={p.email || p.directory}>{p.display_name || p.name} ({p.email || p.directory})</option>)}
                  </select>
                  <button
                    type="button"
                    onClick={handleSyncChromeProfiles}
                    disabled={chromeProfilesSyncing}
                    style={{
                      padding: '0.55rem 0.8rem',
                      background: 'rgba(76,154,255,0.12)',
                      color: chromeProfilesSyncing ? '#666' : '#4C9AFF',
                      border: '1px solid rgba(76,154,255,0.35)',
                      borderRadius: '6px',
                      fontSize: '0.8rem',
                      fontWeight: 600,
                      cursor: chromeProfilesSyncing ? 'not-allowed' : 'pointer',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {chromeProfilesSyncing ? '동기화중' : '동기화'}
                  </button>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                  <label style={{ color: '#888', fontSize: '0.875rem', minWidth: '120px', flexShrink: 0 }}>지메일</label>
                  <input style={{ ...inputStyle, flex: 1 }} placeholder="지메일 주소" value={sourcingForm.memo} onChange={e => setSourcingForm(prev => ({ ...prev, memo: e.target.value }))} />
                </div>
              </>
            )}
          </div>

          {/* 저장 버튼 */}
          <div style={{ marginTop: '1.5rem', display: 'flex', gap: '0.5rem' }}>
            <button
              onClick={handleSourcingSave}
              style={{ padding: '0.625rem 1.75rem', background: '#FF8C00', color: '#fff', border: 'none', borderRadius: '6px', fontWeight: 700, fontSize: '0.875rem', cursor: 'pointer' }}
            >{sourcingEditId ? '계정 수정' : '계정 추가'}</button>
            <button
              onClick={handleFetchAllBalances}
              style={{ padding: '0.625rem 1.25rem', background: 'rgba(76,154,255,0.15)', border: '1px solid rgba(76,154,255,0.3)', color: '#4C9AFF', borderRadius: '6px', fontWeight: 600, fontSize: '0.875rem', cursor: 'pointer' }}
            >잔액 새로고침</button>
          </div>
        </div>

        {/* 우측: 해당 소싱처 계정 리스트 */}
        <div style={{ width: '320px', flexShrink: 0 }}>
          <div style={{ fontSize: '0.82rem', fontWeight: 600, color: '#888', marginBottom: '0.5rem' }}>등록 계정</div>
          {(() => {
            const siteAccounts = sourcingAccounts
              .filter((a: SambaSourcingAccount) => a.site_name === sourcingTab)
              .sort((a: SambaSourcingAccount, b: SambaSourcingAccount) =>
                (a.chrome_profile || '').localeCompare(b.chrome_profile || '', undefined, { numeric: true })
              )
            if (siteAccounts.length === 0) return (
              <div style={{ fontSize: '0.78rem', color: '#555', padding: '0.5rem 0' }}>등록된 계정 없음</div>
            )
            return (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.375rem' }}>
                {siteAccounts.map((a: SambaSourcingAccount) => (
                  <div key={a.id} style={{
                    padding: '0.5rem 0.625rem',
                    background: sourcingEditId === a.id
                      ? 'rgba(255,140,0,0.08)'
                      : a.is_login_default
                        ? 'rgba(81,207,102,0.06)'
                        : 'rgba(255,255,255,0.02)',
                    borderRadius: '6px',
                    border: sourcingEditId === a.id
                      ? '1px solid rgba(255,140,0,0.3)'
                      : a.is_login_default
                        ? '1px solid rgba(81,207,102,0.4)'
                        : '1px solid rgba(45,45,45,0.5)',
                    opacity: a.is_active ? 1 : 0.5,
                  }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.25rem' }}>
                      {/* 자동로그인 기본 계정 라디오 — 사이트당 1개만 선택 가능 (백엔드가 다른 계정 자동 false 처리) */}
                      <input
                        type="radio"
                        name={`login-default-${sourcingTab}`}
                        checked={!!a.is_login_default}
                        onChange={() => sourcingAccountApi.setLoginDefault(a.id).then(() => loadSourcingAccounts())}
                        title="자동로그인 기본 계정"
                        style={{ cursor: 'pointer', accentColor: '#51CF66', flexShrink: 0 }}
                      />
                      <span style={{ flex: 1, fontSize: '0.8rem', fontWeight: 600, color: '#E5E5E5', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{a.account_label}({a.username})</span>
                      {a.is_login_default && (
                        <span style={{ fontSize: '0.65rem', color: '#51CF66', fontWeight: 700, background: 'rgba(81,207,102,0.12)', padding: '0.1rem 0.35rem', borderRadius: '3px' }}>자동로그인</span>
                      )}
                      {sourcingTab === 'MUSINSA' && a.chrome_profile && <span style={{ fontSize: '0.68rem', color: '#888', fontFamily: 'monospace' }}>{a.chrome_profile}</span>}
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.25rem', fontSize: '0.7rem' }}>
                      {sourcingTab === 'MUSINSA' && a.chrome_profile && <span style={{ color: '#666', background: '#1A1A1A', padding: '0.05rem 0.3rem', borderRadius: '3px' }}>{chromeProfiles.find((p: ChromeProfile) => p.email === a.chrome_profile || p.directory === a.chrome_profile)?.display_name || chromeProfiles.find((p: ChromeProfile) => p.email === a.chrome_profile || p.directory === a.chrome_profile)?.name || a.chrome_profile}</span>}
                      {sourcingTab === 'MUSINSA' && a.memo && <span style={{ color: '#888' }}>{a.memo}</span>}
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '0.25rem', fontSize: '0.7rem' }}>
                      {(a.additional_fields as Record<string, unknown>)?.cookie_expired ? (
                        <span style={{ color: '#FF6B6B', fontWeight: 600 }}>쿠키 만료 — 재로그인 필요</span>
                      ) : (
                        <>
                          <span style={{ color: '#51CF66', fontWeight: 600 }}>머니 {fmtNum(a.balance ?? 0)}</span>
                          <span style={{ color: '#4C9AFF', fontWeight: 600 }}>적립금 {fmtNum(Number((a.additional_fields as Record<string, unknown>)?.mileage ?? 0))}</span>
                        </>
                      )}
                      {a.balance_updated_at && <span style={{ color: '#666' }}>{new Date(a.balance_updated_at).toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })}</span>}
                    </div>
                    <div style={{ display: 'flex', gap: '0.25rem' }}>
                      <button onClick={() => handleFetchBalance(a.id)} disabled={balanceLoading[a.id]} style={{ padding: '0.15rem 0.4rem', fontSize: '0.68rem', background: 'rgba(81,207,102,0.1)', border: '1px solid rgba(81,207,102,0.3)', color: '#51CF66', borderRadius: '4px', cursor: 'pointer', opacity: balanceLoading[a.id] ? 0.5 : 1 }}>{balanceLoading[a.id] ? '조회중' : '잔액'}</button>
                      <button onClick={() => sourcingAccountApi.toggle(a.id).then(() => loadSourcingAccounts())} style={{ padding: '0.15rem 0.4rem', fontSize: '0.68rem', background: a.is_active ? 'rgba(76,154,255,0.1)' : 'rgba(100,100,100,0.2)', border: `1px solid ${a.is_active ? 'rgba(76,154,255,0.3)' : '#555'}`, color: a.is_active ? '#4C9AFF' : '#888', borderRadius: '4px', cursor: 'pointer' }}>{a.is_active ? 'ON' : 'OFF'}</button>
                      <button
                        onClick={() => handleSourcingEdit(a)}
                        style={{
                          padding: '0.15rem 0.4rem', fontSize: '0.68rem', borderRadius: '4px', cursor: 'pointer',
                          background: sourcingEditId === a.id ? 'rgba(255,140,0,0.15)' : 'rgba(60,60,60,0.8)',
                          color: sourcingEditId === a.id ? '#FF8C00' : '#C5C5C5',
                          border: sourcingEditId === a.id ? '1px solid #FF8C00' : '1px solid #3D3D3D',
                        }}
                      >{sourcingEditId === a.id ? '수정중' : '수정'}</button>
                      <button onClick={() => handleSourcingDelete(a.id)} style={{ padding: '0.15rem 0.4rem', fontSize: '0.68rem', background: 'rgba(255,80,80,0.15)', color: '#FF6B6B', border: '1px solid rgba(255,80,80,0.3)', borderRadius: '4px', cursor: 'pointer' }}>삭제</button>
                    </div>
                  </div>
                ))}
              </div>
            )
          })()}
        </div>
      </div>
    </div>
  )
}
