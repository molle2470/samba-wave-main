'use client'

import React, { useState, useCallback } from 'react'
import {
  collectorApi,
  type SambaCollectedProduct,
} from '@/lib/samba/api/commerce'
import { fmtNum } from '@/lib/samba/styles'

/** 옵션 패널 — 옵션명/가격/재고 편집 + 일괄수정. */
const OptionPanel = React.memo(function OptionPanel({
  options,
  productCost,
  productId,
  sourceSite,
  nameRule,
  curSym = '₩',
}: {
  options: unknown[]
  productCost: number
  productId: string
  sourceSite: string
  nameRule?: import('@/lib/samba/api/support').SambaNameRule
  curSym?: string
}) {
  const [open, setOpen] = useState(false)
  const [selectAll, setSelectAll] = useState(true)
  const [editingName, setEditingName] = useState<number | null>(null)
  const [localOpts, setLocalOpts] = useState(options as Record<string, unknown>[])
  const [bulkModal, setBulkModal] = useState<'price' | 'stock' | 'addOption' | null>(null)
  const [bulkValue, setBulkValue] = useState('')
  // 개별 옵션 가격/재고 편집 상태 (인덱스 → 표시값)
  const [editingPrices, setEditingPrices] = useState<Record<number, string>>({})
  const [editingStocks, setEditingStocks] = useState<Record<number, string>>({})
  const opts = localOpts
  const displayOptionName = useCallback((value: unknown) => {
    const text = String(value ?? '')
    const rules = nameRule?.option_rules
    if (!Array.isArray(rules) || rules.length === 0) return text
    return rules.reduce((acc, rule) => {
      if (!rule || typeof rule !== 'object') return acc
      const from = String((rule as Record<string, unknown>).from ?? '')
      const to = String((rule as Record<string, unknown>).to ?? '')
      return from ? acc.split(from).join(to) : acc
    }, text)
  }, [nameRule])
  const getOptionBasePrice = useCallback((opt: Record<string, unknown>) => {
    const rawPrice = Number(opt.price ?? opt.salePrice ?? 0)
    if (rawPrice > 0) return rawPrice
    return productCost > 0 ? productCost : 0
  }, [productCost])

  // 옵션 변경 시 즉시 API 저장
  const saveOptions = useCallback((newOpts: Record<string, unknown>[]) => {
    setLocalOpts(newOpts)
    collectorApi.updateProduct(productId, { options: newOpts } as Partial<SambaCollectedProduct>).catch(() => {})
  }, [productId])

  // 일괄수정 적용 (가격 또는 재고)
  const applyBulk = useCallback((mode: 'price' | 'stock', value: string) => {
    const v = parseInt(value, 10)
    if (isNaN(v)) return
    if (mode === 'price') {
      // React 상태로 가격 입력값 일괄 갱신
      const newPrices: Record<number, string> = {}
      opts.forEach((_, idx) => { newPrices[idx] = fmtNum(v) })
      setEditingPrices(newPrices)
      saveOptions(opts.map(o => ({ ...o, price: v, salePrice: v })))
    } else {
      // React 상태로 재고 입력값 일괄 갱신
      const newStocks: Record<number, string> = {}
      opts.forEach((_, idx) => { newStocks[idx] = String(v) })
      setEditingStocks(newStocks)
      saveOptions(opts.map(o => ({ ...o, stock: v })))
    }
  }, [opts, saveOptions])

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
        <span style={{ color: '#888', fontSize: '0.78rem' }}>{fmtNum(opts.length)}개 옵션</span>
        <button
          onClick={() => setOpen(!open)}
          style={{
            fontSize: '0.7rem', padding: '2px 8px',
            border: '1px solid #2D2D2D', borderRadius: '4px',
            color: '#888', background: 'transparent', cursor: 'pointer',
          }}
        >
          {open ? '접기' : '펼치기'}
        </button>
      </div>
      {open && (
        <div style={{ marginTop: '8px' }}>
          {/* 안내문구 */}
          <p style={{ fontSize: '0.72rem', color: '#888', marginBottom: '0.75rem', lineHeight: 1.5 }}>
            ※ 옵션별로 가격 및 재고 수정이 가능합니다. 가격/재고를 수정하시면 해외 가격/재고는 무시되고, 수정하신 가격/재고로 반영됩니다.<br />
            ※ 체크박스에 체크되어 있는 상품만 마켓으로 전송됩니다. 전송을 원하지 않는 옵션은 체크를 해제하신 후 옵션저장 버튼을 클릭해주세요.
          </p>

          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ borderBottom: '1px solid #2D2D2D' }}>
                <th style={{ width: '36px', padding: '0.5rem', textAlign: 'center' }}>
                  <input type="checkbox" checked={selectAll} onChange={(e) => setSelectAll(e.target.checked)} style={{ cursor: 'pointer', accentColor: '#FF8C00' }} />
                </th>
                <th style={{ padding: '0.5rem', textAlign: 'center', fontSize: '0.8rem', color: '#999', fontWeight: 500 }}>
                  옵션명
                  <button
                    onClick={() => setEditingName(editingName === -1 ? null : -1)}
                    style={{ marginLeft: '0.4rem', fontSize: '0.7rem', padding: '1px 6px', background: editingName === -1 ? 'rgba(255,140,0,0.3)' : 'rgba(255,140,0,0.15)', color: '#FF8C00', border: '1px solid rgba(255,140,0,0.3)', borderRadius: '3px', cursor: 'pointer' }}
                  >{editingName === -1 ? '편집완료' : '옵션명변경'}</button>
                  <button
                    onClick={() => { setBulkModal('addOption'); setBulkValue('') }}
                    style={{ marginLeft: '0.3rem', fontSize: '0.7rem', padding: '1px 6px', background: 'rgba(255,255,255,0.05)', color: '#C5C5C5', border: '1px solid #3D3D3D', borderRadius: '3px', cursor: 'pointer' }}
                  >옵션추가</button>
                </th>
                <th style={{ padding: '0.5rem', textAlign: 'center', fontSize: '0.8rem', color: '#999', fontWeight: 500 }}>
                  원가<br /><span style={{ fontSize: '0.7rem', color: '#555', fontWeight: 400 }}>(일반배송)</span>
                </th>
                {sourceSite === 'KREAM' && (
                  <th style={{ padding: '0.5rem', textAlign: 'center', fontSize: '0.8rem', color: '#999', fontWeight: 500 }}>
                    빠른배송<br /><span style={{ fontSize: '0.7rem', color: '#555', fontWeight: 400 }}>(KREAM)</span>
                  </th>
                )}
                <th style={{ padding: '0.5rem', textAlign: 'center', fontSize: '0.8rem', color: '#999', fontWeight: 500 }}>
                  상품가<br />
                  <button
                    onClick={() => { setBulkModal('price'); setBulkValue('') }}
                    style={{ fontSize: '0.7rem', padding: '1px 6px', background: 'rgba(255,255,255,0.05)', color: '#C5C5C5', border: '1px solid #3D3D3D', borderRadius: '3px', cursor: 'pointer', marginTop: '2px' }}
                  >일괄수정</button>
                </th>
                <th style={{ padding: '0.5rem', textAlign: 'center', fontSize: '0.8rem', color: '#999', fontWeight: 500 }}>
                  옵션재고
                  <button
                    onClick={() => { setBulkModal('stock'); setBulkValue('') }}
                    style={{ marginLeft: '0.3rem', fontSize: '0.7rem', padding: '1px 6px', background: 'rgba(255,255,255,0.05)', color: '#C5C5C5', border: '1px solid #3D3D3D', borderRadius: '3px', cursor: 'pointer' }}
                  >일괄수정</button>
                </th>
              </tr>
            </thead>
            <tbody>
              {opts.map((o, idx) => {
                const optionName = displayOptionName(o.name || o.value || `option${idx + 1}`)
                const isBrandDelivery = o.isBrandDelivery === true
                const stock = o.stock !== undefined && o.stock !== null ? Number(o.stock) : -1
                const isSoldOut = !isBrandDelivery && (o.isSoldOut === true || stock === 0)
                const optionCost = isSoldOut ? 0 : getOptionBasePrice(o)
                const isChecked = !isSoldOut

                // 가격 표시: 편집 상태값 > 옵션 저장가 > 기본가
                const priceDisplay = editingPrices[idx] ?? (
                  optionCost > 0 ? fmtNum(optionCost) : '0'
                )

                let stockDisplay: React.ReactNode
                if (isBrandDelivery) {
                  stockDisplay = <span style={{ color: '#6B8AFF', fontWeight: 600, fontSize: '0.78rem' }}>브랜드배송</span>
                } else if (isSoldOut) {
                  stockDisplay = <span style={{ color: '#FF6B6B', fontWeight: 600 }}>품절</span>
                } else if (stock < 0 || stock >= 999) {
                  stockDisplay = (
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: '4px' }}>
                      <input
                        type="number"
                        value={editingStocks[idx] ?? ''}
                        placeholder=""
                        onChange={(e) => setEditingStocks(prev => ({ ...prev, [idx]: e.target.value }))}
                        onBlur={(e) => {
                          const v = parseInt(e.target.value, 10)
                          if (!isNaN(v)) {
                            const newOpts = [...opts]
                            newOpts[idx] = { ...newOpts[idx], stock: v, isSoldOut: v === 0 }
                            saveOptions(newOpts)
                          }
                        }}
                        style={{ width: '70px', background: 'rgba(255,255,255,0.05)', border: '1px solid #3D3D3D', color: '#E5E5E5', borderRadius: '4px', padding: '2px 6px', textAlign: 'right', fontSize: '0.875rem' }}
                      />
                      <span style={{ fontSize: '0.72rem', color: '#51CF66' }}>{stock >= 999 ? '충분' : '재고있음'}</span>
                    </span>
                  )
                } else {
                  stockDisplay = (
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: '4px' }}>
                      <input
                        type="number"
                        value={editingStocks[idx] ?? String(stock)}
                        onChange={(e) => setEditingStocks(prev => ({ ...prev, [idx]: e.target.value }))}
                        onBlur={(e) => {
                          const v = parseInt(e.target.value, 10)
                          if (!isNaN(v)) {
                            const newOpts = [...opts]
                            newOpts[idx] = { ...newOpts[idx], stock: v, isSoldOut: v === 0 }
                            saveOptions(newOpts)
                          }
                        }}
                        style={{ width: '60px', background: 'rgba(255,255,255,0.05)', border: '1px solid #3D3D3D', color: '#E5E5E5', borderRadius: '4px', padding: '2px 6px', textAlign: 'right', fontSize: '0.875rem' }}
                      />
                    </span>
                  )
                }

                return (
                  <tr key={idx} style={{ borderBottom: '1px solid rgba(45,45,45,0.5)', opacity: isSoldOut ? 0.5 : 1 }}>
                    <td style={{ padding: '0.5rem', textAlign: 'center' }}>
                      <input type="checkbox" defaultChecked={isChecked} style={{ cursor: 'pointer', accentColor: '#FF8C00' }} />
                    </td>
                    <td style={{ padding: '0.5rem', fontSize: '0.875rem', color: '#E5E5E5' }}>
                      {editingName === -1 ? (
                        <input
                          type="text"
                          defaultValue={String(o.name || o.value || `옵션${idx + 1}`)}
                          onBlur={(e) => {
                            const newOpts = [...opts]
                            newOpts[idx] = { ...newOpts[idx], name: e.target.value }
                            saveOptions(newOpts)
                          }}
                          style={{ width: '100%', background: 'rgba(255,255,255,0.05)', border: '1px solid #FF8C00', color: '#E5E5E5', borderRadius: '4px', padding: '2px 6px', fontSize: '0.875rem' }}
                        />
                      ) : (
                        optionName
                      )}
                    </td>
                    <td style={{ padding: '0.5rem', textAlign: 'right', fontSize: '0.875rem', color: '#C5C5C5' }}>
                      {sourceSite === 'KREAM'
                        ? (Number(o.kreamGeneralPrice || o.kreamNormalPrice || o.price || 0) > 0 ? `${curSym}${fmtNum(Number(o.kreamGeneralPrice || o.kreamNormalPrice || o.price || 0))}` : '-')
                        : (optionCost > 0 ? `${curSym}${fmtNum(optionCost)}` : '-')
                      }
                    </td>
                    {sourceSite === 'KREAM' && (
                      <td style={{ padding: '0.5rem', textAlign: 'right', fontSize: '0.875rem', color: '#6B8AFF' }}>
                        {Number(o.kreamFastPrice || 0) > 0 ? `${curSym}${fmtNum(Number(o.kreamFastPrice))}` : '-'}
                      </td>
                    )}
                    <td style={{ padding: '0.5rem', textAlign: 'right', fontSize: '0.875rem', color: '#E5E5E5', whiteSpace: 'nowrap' }}>
                      <input
                        type="text"
                        inputMode="numeric"
                        value={priceDisplay}
                        onChange={(e) => {
                          setEditingPrices(prev => ({ ...prev, [idx]: e.target.value }))
                        }}
                        onFocus={(e) => {
                          // 포커스 시 콤마 제거하여 편집 용이하게
                          setEditingPrices(prev => ({ ...prev, [idx]: e.target.value.replace(/,/g, '') }))
                        }}
                        onBlur={(e) => {
                          // 블러 시 숫자 포맷팅 적용
                          const v = parseInt(e.target.value.replace(/,/g, ''), 10)
                          setEditingPrices(prev => ({ ...prev, [idx]: isNaN(v) ? '0' : fmtNum(v) }))
                          if (!isNaN(v)) {
                            const newOpts = [...opts]
                            newOpts[idx] = { ...newOpts[idx], price: v, salePrice: v }
                            saveOptions(newOpts)
                          }
                        }}
                        style={{ width: '80px', background: 'rgba(255,255,255,0.05)', border: '1px solid #3D3D3D', color: '#E5E5E5', borderRadius: '4px', padding: '2px 6px', textAlign: 'right', fontSize: '0.875rem' }}
                      />
                      <span style={{ marginLeft: '2px' }}>{curSym === '$' ? '$' : '원'}</span>
                    </td>
                    <td style={{ padding: '0.5rem', textAlign: 'right', fontSize: '0.875rem', color: '#E5E5E5' }}>
                      {stockDisplay}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>

          {/* 일괄수정 모달 */}
          {bulkModal && (
            <div style={{
              position: 'fixed', inset: 0, zIndex: 99998,
              background: 'rgba(0,0,0,0.6)', display: 'flex',
              alignItems: 'center', justifyContent: 'center',
            }} onClick={() => setBulkModal(null)}>
              <div style={{
                background: '#1E1E1E', border: '1px solid #3D3D3D', borderRadius: '10px',
                padding: '20px 24px', width: 'min(360px, 90vw)',
              }} onClick={e => e.stopPropagation()}>
                <h4 style={{ margin: '0 0 12px', fontSize: '0.85rem', color: '#E5E5E5' }}>
                  {bulkModal === 'price' ? '상품가 일괄수정' : bulkModal === 'stock' ? '옵션재고 일괄수정' : '옵션 추가'}
                </h4>
                <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                  <input
                    type="text"
                    inputMode={bulkModal === 'addOption' ? 'text' : 'numeric'}
                    autoFocus
                    placeholder={bulkModal === 'price' ? '가격 입력 (원)' : bulkModal === 'stock' ? '재고 입력 (개)' : '옵션명 입력'}
                    value={bulkValue}
                    onChange={e => setBulkValue(bulkModal === 'addOption' ? e.target.value : e.target.value.replace(/[^0-9]/g, ''))}
                    onKeyDown={e => {
                      if (e.key !== 'Enter') return
                      if (bulkModal === 'addOption') {
                        if (bulkValue.trim()) {
                          saveOptions([...opts, { name: bulkValue.trim(), price: productCost, stock: 0, isSoldOut: false }])
                          setBulkModal(null)
                        }
                      } else {
                        applyBulk(bulkModal, bulkValue)
                        setBulkModal(null)
                      }
                    }}
                    style={{ flex: 1, padding: '8px 12px', fontSize: '0.85rem', background: '#1A1A1A', border: '1px solid #3D3D3D', color: '#E5E5E5', borderRadius: '6px' }}
                  />
                  {bulkModal !== 'addOption' && <span style={{ color: '#888', fontSize: '0.8rem' }}>{bulkModal === 'price' ? (curSym === '$' ? '$' : '원') : '개'}</span>}
                </div>
                <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px', marginTop: '16px' }}>
                  <button onClick={() => setBulkModal(null)}
                    style={{ padding: '6px 16px', fontSize: '0.8rem', borderRadius: '6px', border: '1px solid #3D3D3D', background: 'transparent', color: '#888', cursor: 'pointer' }}>취소</button>
                  <button onClick={() => {
                    if (bulkModal === 'addOption') {
                      if (bulkValue.trim()) {
                        saveOptions([...opts, { name: bulkValue.trim(), price: productCost, stock: 0, isSoldOut: false }])
                      }
                    } else {
                      applyBulk(bulkModal, bulkValue)
                    }
                    setBulkModal(null)
                  }} style={{ padding: '6px 16px', fontSize: '0.8rem', borderRadius: '6px', border: 'none', background: '#FF8C00', color: '#fff', cursor: 'pointer', fontWeight: 600 }}>
                    {bulkModal === 'addOption' ? '추가' : '적용'}
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
})

export default OptionPanel
