'use client'
import { useMemo, useState } from 'react'
import AccountBlock from './AccountBlock'
import type { TetrisAccountBlock, TetrisMarketGroup, TetrisBrandBlock } from '@/lib/samba/api/tetris'
import type { DragState } from './useTetris'

interface Policy {
  id: string
  name: string
  color: string
}

interface Props {
  market: TetrisMarketGroup
  pixelsPerUnit: number
  globalMax: number
  policies: Policy[]
  dragState: DragState
  onDragStart: (block: TetrisBrandBlock, accountId: string) => void
  onDrop: (toAccountId: string) => Promise<void>
  onReorder: (draggedId: string, newIndex: number, allAssignments: TetrisBrandBlock[]) => Promise<void>
  onAccountReorder: (accounts: TetrisAccountBlock[]) => Promise<void>
  onRemove: (assignmentId: string, brandName: string, sourceSite: string) => void
  onDeleteBrandScope: (sourceSite: string, brandName: string) => Promise<void>
  onRemoveLegacyFromAccount: (sourceSite: string, brandName: string, accountId: string) => Promise<void>
  onPolicyChange: (assignmentId: string, policyId: string | null, accountId: string) => Promise<void>
  onToggleExcluded: (block: TetrisBrandBlock, accountId: string) => Promise<void>
}

function AccountSlot({
  active,
  onEnter,
  onLeave,
  onDrop,
}: {
  active: boolean
  onEnter: () => void
  onLeave: () => void
  onDrop: () => void
}) {
  return (
    <div
      style={{
        height: active ? 10 : 4,
        background: active ? '#FF8C00' : 'rgba(255,140,0,0.15)',
        borderRadius: 3,
        margin: '2px 0',
        transition: 'height 0.1s, background 0.1s',
        flexShrink: 0,
      }}
      onDragOver={e => { e.preventDefault(); e.stopPropagation(); onEnter() }}
      onDragLeave={onLeave}
      onDrop={e => { e.preventDefault(); e.stopPropagation(); onDrop() }}
    />
  )
}

export default function MarketColumn({
  market,
  pixelsPerUnit,
  globalMax,
  policies,
  dragState,
  onDragStart,
  onDrop,
  onReorder,
  onAccountReorder,
  onRemove,
  onDeleteBrandScope,
  onRemoveLegacyFromAccount,
  onPolicyChange,
  onToggleExcluded,
}: Props) {
  const [draggedAccountId, setDraggedAccountId] = useState<string | null>(null)
  const [dropIndex, setDropIndex] = useState<number | null>(null)

  const orderedAccounts = useMemo(() => {
    return [...market.accounts].sort((a, b) => {
      // 수집상품수 오름차순 — 수집 많은 계정이 하단에 배치
      if (a.total_collected !== b.total_collected) {
        return a.total_collected - b.total_collected
      }
      return a.account_label.localeCompare(b.account_label)
    })
  }, [market.accounts])

  const columnHeight = Math.max(60, Math.round(globalMax * pixelsPerUnit))
  const isAccountDragging = draggedAccountId !== null

  const handleAccountDrop = async (targetIndex: number) => {
    if (!draggedAccountId) return
    const reordered = [...orderedAccounts]
    const fromIndex = reordered.findIndex(account => account.account_id === draggedAccountId)
    if (fromIndex === -1) {
      setDraggedAccountId(null)
      setDropIndex(null)
      return
    }

    const [moved] = reordered.splice(fromIndex, 1)
    reordered.splice(targetIndex, 0, moved)

    setDraggedAccountId(null)
    setDropIndex(null)
    await onAccountReorder(reordered)
  }

  return (
    <div style={{ minWidth: 211, width: 227, flexShrink: 0 }}>
      <div style={{
        background: 'rgba(20,20,20,0.5)',
        border: '1px solid #333',
        borderRadius: 6,
        padding: '0 6px',
      }}>
        <div style={{
          minHeight: columnHeight,
          display: 'flex',
          flexDirection: 'column',
          justifyContent: 'flex-end',
        }}>
          {isAccountDragging && (
            <AccountSlot
              active={dropIndex === 0}
              onEnter={() => setDropIndex(0)}
              onLeave={() => setDropIndex(null)}
              onDrop={() => handleAccountDrop(0)}
            />
          )}
          {orderedAccounts.map((account, index) => {
            const scaledCapacityHeight = account.max_count > 0
              ? Math.max(1, Math.round(account.max_count * pixelsPerUnit))
              : 0
            const capacityHeight = scaledCapacityHeight > 0
              ? scaledCapacityHeight
              : account.total_collected > 0
                ? Math.max(1, Math.round(account.total_collected * pixelsPerUnit))
                : 60

            return (
              <div key={account.account_id}>
                <AccountBlock
                  account={account}
                  capacityHeight={capacityHeight}
                  pixelsPerUnit={pixelsPerUnit}
                  policies={policies}
                  dragState={dragState}
                  onDragStart={onDragStart}
                  onDrop={onDrop}
                  onReorder={onReorder}
                  onRemove={onRemove}
                  onDeleteBrandScope={onDeleteBrandScope}
                  onRemoveLegacyFromAccount={onRemoveLegacyFromAccount}
                  onPolicyChange={onPolicyChange}
                  onToggleExcluded={onToggleExcluded}
                  isDragging={dragState !== null}
                  isAccountDragging={isAccountDragging}
                  onAccountDragStart={accountId => setDraggedAccountId(accountId)}
                  onAccountDragEnd={() => {
                    setDraggedAccountId(null)
                    setDropIndex(null)
                  }}
                />
                {isAccountDragging && (
                  <AccountSlot
                    active={dropIndex === index + 1}
                    onEnter={() => setDropIndex(index + 1)}
                    onLeave={() => setDropIndex(null)}
                    onDrop={() => handleAccountDrop(index + 1)}
                  />
                )}
              </div>
            )
          })}
          {orderedAccounts.length === 0 && (
            <div style={{ color: '#444', fontSize: 11, padding: '12px 0', textAlign: 'center' }}>No accounts</div>
          )}
        </div>
      </div>
    </div>
  )
}
