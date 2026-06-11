'use client'

import { Dispatch, SetStateAction, useCallback, useEffect, useState } from 'react'
import { card, fmtNum } from '@/lib/samba/styles'
import { forbiddenApi, type SambaForbiddenWord } from '@/lib/samba/api/commerce'
import { MARKETS } from '@/lib/samba/markets'
import { showAlert } from '@/components/samba/Modal'

interface Props {
  // 아래 텍스트/세터 props 는 레거시 호환용 — 본 섹션은 마켓별 상태를 자체 관리한다.
  forbiddenText?: string
  deletionText?: string
  optionDeletionText?: string
  wordsSaving?: boolean
  setForbiddenText?: (v: string) => void
  setDeletionText?: (v: string) => void
  setOptionDeletionText?: (v: string) => void
  setWordsSaving?: (v: boolean) => void
  setInitialForbiddenText?: (v: string) => void
  setInitialDeletionText?: (v: string) => void
  setInitialOptionDeletionText?: (v: string) => void
  tagBanned: { rejected: string[]; brands: string[]; source_sites: string[] }
  setTagBanned: Dispatch<SetStateAction<{ rejected: string[]; brands: string[]; source_sites: string[] }>>
}

// 공통 + 전송 대상 마켓(카테고리 전용 제외)
const MARKET_OPTIONS: { value: string; label: string }[] = [
  { value: 'common', label: '공통 (모든 마켓)' },
  ...MARKETS.filter(m => !m.categoryOnly).map(m => ({ value: m.id, label: m.label })),
]

const wordsToText = (words: SambaForbiddenWord[], type: string) =>
  [...new Set(words.filter(w => w.type === type).map(w => w.word.trim()).filter(Boolean))].join('; ')

export function ForbiddenWordsSection({ tagBanned, setTagBanned }: Props) {
  const [market, setMarket] = useState('common')
  const [forbiddenText, setForbiddenText] = useState('')
  const [deletionText, setDeletionText] = useState('')
  const [optionDeletionText, setOptionDeletionText] = useState('')
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState('')  // 저장 중인 type ('' = 없음)

  // 선택 마켓의 단어 로드
  const loadWords = useCallback((mk: string) => {
    setLoading(true)
    forbiddenApi.listWords(undefined, mk)
      .then((words: SambaForbiddenWord[]) => {
        setForbiddenText(wordsToText(words, 'forbidden'))
        setDeletionText(wordsToText(words, 'deletion'))
        setOptionDeletionText(wordsToText(words, 'option_deletion'))
      })
      .catch(() => { showAlert('금지어 로드 실패', 'error') })
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { loadWords(market) }, [market, loadWords])

  const save = async (type: string, text: string) => {
    setSaving(type)
    try {
      const words = [...new Set(text.split(';').map(w => w.trim()).filter(Boolean))]
      await forbiddenApi.bulkSaveWords(type, words, market)
      const deduped = words.join('; ')
      if (type === 'forbidden') setForbiddenText(deduped)
      else if (type === 'deletion') setDeletionText(deduped)
      else setOptionDeletionText(deduped)
      const label = type === 'forbidden' ? '금지어' : type === 'deletion' ? '삭제어' : '옵션삭제어'
      const mkLabel = MARKET_OPTIONS.find(m => m.value === market)?.label ?? market
      showAlert(`[${mkLabel}] ${label} ${fmtNum(words.length)}개 저장 완료`, 'success')
    } catch {
      showAlert('저장 실패', 'error')
    }
    setSaving('')
  }

  const isCommon = market === 'common'

  // 입력 컬럼 1개 렌더
  const column = (
    type: string, title: string, color: string, bg: string, border: string,
    value: string, setValue: (v: string) => void, placeholder: string, note?: string,
  ) => (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.4rem' }}>
        <div style={{ fontSize: '0.8125rem', color, fontWeight: 600 }}>{title}</div>
        <button
          disabled={saving !== '' || loading}
          onClick={() => save(type, value)}
          style={{
            padding: '0.25rem 0.75rem', borderRadius: '4px', fontSize: '0.75rem', fontWeight: 600,
            background: bg, border: `1px solid ${border}`, color, cursor: 'pointer',
          }}
        >{saving === type ? '...' : '저장'}</button>
      </div>
      <textarea
        value={value}
        onChange={e => setValue(e.target.value)}
        placeholder={placeholder}
        style={{
          width: '100%', height: '100px', background: '#0A0A0A', border: '1px solid #2D2D2D',
          borderRadius: '6px', padding: '8px', color: '#E5E5E5', fontSize: '0.8125rem',
          resize: 'vertical', fontFamily: 'monospace',
        }}
      />
      <div style={{ fontSize: '0.75rem', color: '#666', marginTop: '2px' }}>
        {fmtNum(value.split(';').filter(w => w.trim()).length)}개
        {note && <span style={{ marginLeft: '0.5rem', color: '#7a6a3a' }}>{note}</span>}
      </div>
    </div>
  )

  return (
    <>
      {/* 금지어 / 삭제어 (마켓별) */}
      <div style={{ ...card, padding: '1.5rem', marginTop: '1.25rem' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '1rem', flexWrap: 'wrap' }}>
          <span style={{ fontSize: '0.9375rem', fontWeight: 700, color: '#E5E5E5' }}>금지어 / 삭제어</span>
          <select
            value={market}
            onChange={e => setMarket(e.target.value)}
            style={{
              background: '#1A1A1A', border: '1px solid #3D3D3D', borderRadius: '6px',
              color: '#E5E5E5', fontSize: '0.8125rem', padding: '0.3rem 0.6rem', cursor: 'pointer',
            }}
          >
            {MARKET_OPTIONS.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
          </select>
          <span style={{ fontSize: '0.8125rem', color: '#666' }}>
            {isCommon ? '모든 마켓에 공통 적용' : '공통 + 이 마켓에만 추가 적용'}
          </span>
          {loading && <span style={{ fontSize: '0.75rem', color: '#888' }}>불러오는 중…</span>}
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '1rem' }}>
          {column('forbidden', '금지어 (포함 시 해당 마켓 전송 제외) — 세미콜론(;) 구분', '#FF6B6B',
            'rgba(255,107,107,0.12)', 'rgba(255,107,107,0.3)',
            forbiddenText, setForbiddenText, '구찌; 루이비통; 샤넬; 프라다')}
          {column('deletion', '삭제어 — 상품명에서 자동 제거', '#FFB84D',
            'rgba(255,184,77,0.12)', 'rgba(255,184,77,0.3)',
            deletionText, setDeletionText, '매장정품; 정품; 해외직구; 무료배송')}
          {column('option_deletion', '옵션삭제어 — 옵션명에서 자동 제거', '#A29BFE',
            'rgba(162,155,254,0.12)', 'rgba(162,155,254,0.3)',
            optionDeletionText, setOptionDeletionText, '01(; 02(; ); [품절]',
            market !== 'common' && market !== 'smartstore' ? '※ 옵션삭제어는 현재 스마트스토어 전송에만 적용됨' : undefined)}
        </div>
      </div>

      {/* 태그 금지어 (스마트스토어 등록불가 단어) */}
      <div style={{ ...card, padding: '1.5rem', marginTop: '1.25rem' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '1rem', flexWrap: 'wrap' }}>
          <span style={{ fontSize: '0.9375rem', fontWeight: 700, color: '#C4736E' }}>태그 금지어</span>
          <span style={{ fontSize: '0.8125rem', color: '#666' }}>** 스마트스토어 등록 시 자동 제외되는 단어 (API 거부 + 소싱처 + 브랜드)</span>
          <button onClick={() => forbiddenApi.getTagBannedWords().then(setTagBanned).catch(() => {})}
            style={{ marginLeft: 'auto', background: 'rgba(50,50,50,0.8)', border: '1px solid #3D3D3D', color: '#C5C5C5', padding: '0.3rem 0.875rem', borderRadius: '6px', fontSize: '0.8125rem', cursor: 'pointer' }}>새로고침</button>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          <div>
            <div style={{ fontSize: '0.8125rem', color: '#C4736E', fontWeight: 600, marginBottom: '0.4rem' }}>
              API 거부 태그 ({fmtNum(tagBanned.rejected.length)}개)
              <span style={{ fontWeight: 400, color: '#666', marginLeft: '0.5rem' }}>전송 실패 시 자동 누적 + 직접 추가 가능</span>
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px', alignItems: 'center' }}>
              {tagBanned.rejected.length === 0 && <span style={{ fontSize: '0.75rem', color: '#555' }}>아직 없음</span>}
              {tagBanned.rejected.map((w, i) => (
                <span key={i} style={{
                  fontSize: '0.7rem', padding: '2px 8px', borderRadius: '10px',
                  background: 'rgba(196,115,110,0.12)', border: '1px solid rgba(196,115,110,0.3)', color: '#C4736E',
                  display: 'inline-flex', alignItems: 'center', gap: '4px',
                }}>
                  {w}
                  <span style={{ cursor: 'pointer', color: '#888', fontSize: '0.8rem', lineHeight: 1 }}
                    onClick={async () => {
                      const updated = tagBanned.rejected.filter((_, idx) => idx !== i)
                      await forbiddenApi.saveSetting('smartstore_banned_tags', updated)
                      setTagBanned(prev => ({ ...prev, rejected: updated }))
                    }}>×</span>
                </span>
              ))}
              <input
                type="text"
                placeholder="금지어 입력 후 Enter"
                style={{ fontSize: '0.7rem', padding: '2px 7px', border: '1px solid #2D2D2D', borderRadius: '4px', color: '#C5C5C5', background: '#1A1A1A', outline: 'none', width: '140px' }}
                onKeyDown={async (e) => {
                  if (e.key === 'Enter') {
                    const val = e.currentTarget.value.trim().toLowerCase()
                    if (!val || tagBanned.rejected.includes(val)) return
                    const updated = [...tagBanned.rejected, val]
                    await forbiddenApi.saveSetting('smartstore_banned_tags', updated)
                    setTagBanned(prev => ({ ...prev, rejected: updated }))
                    e.currentTarget.value = ''
                  }
                }}
              />
            </div>
          </div>
          <div>
            <div style={{ fontSize: '0.8125rem', color: '#FFB84D', fontWeight: 600, marginBottom: '0.4rem' }}>
              수집 브랜드 ({fmtNum(tagBanned.brands.length)}개)
              <span style={{ fontWeight: 400, color: '#666', marginLeft: '0.5rem' }}>브랜드명 포함 태그 자동 제외</span>
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px', maxHeight: '80px', overflow: 'auto' }}>
              {tagBanned.brands.map((w, i) => (
                <span key={i} style={{ fontSize: '0.7rem', padding: '2px 8px', borderRadius: '10px', background: 'rgba(255,184,77,0.08)', border: '1px solid rgba(255,184,77,0.25)', color: '#FFB84D' }}>{w}</span>
              ))}
            </div>
          </div>
          <div>
            <div style={{ fontSize: '0.8125rem', color: '#4C9AFF', fontWeight: 600, marginBottom: '0.4rem' }}>
              소싱처 ({fmtNum(tagBanned.source_sites.length)}개)
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px' }}>
              {tagBanned.source_sites.map((w, i) => (
                <span key={i} style={{ fontSize: '0.7rem', padding: '2px 8px', borderRadius: '10px', background: 'rgba(76,154,255,0.08)', border: '1px solid rgba(76,154,255,0.25)', color: '#4C9AFF' }}>{w}</span>
              ))}
            </div>
          </div>
        </div>
      </div>
    </>
  )
}
