'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { rewardsApi, type RewardAccountRow, type RewardJob, type RewardsStatus } from '@/lib/samba/api'
import { getDeviceId } from '@/lib/samba/deviceId'
import { fmtNum } from '@/lib/samba/styles'

function formatRelative(iso: string | null): string {
  if (!iso) return '-'
  const dt = new Date(iso)
  if (Number.isNaN(dt.getTime())) return '-'
  return dt.toLocaleString('ko-KR', {
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function isFresh24h(iso: string | null): boolean {
  if (!iso) return false
  const dt = new Date(iso).getTime()
  return !Number.isNaN(dt) && Date.now() - dt < 24 * 3600 * 1000
}

const ACTION_LABEL: Record<string, string> = {
  musinsa_attendance: '출석체크',
  musinsa_snap_like: '스냅 좋아요',
  musinsa_balance: '잔액 갱신',
  musinsa_review: '리뷰 자동작성',
  abcmart_attendance: '출석체크',
  abcmart_review: '리뷰 자동작성',
  ssg_review: '리뷰 자동작성',
  gs_review: '리뷰 자동작성',
  lotteon_review: '리뷰 자동작성',
  naver_review: '리뷰 자동작성',
  kream_review: '리뷰 자동작성',
}

const JOB_STATUS_VIEW: Record<string, { label: string; color: string; bg: string }> = {
  pending: { label: '적재됨', color: '#A0A0A0', bg: 'rgba(160,160,160,0.12)' },
  dispatched: { label: '실행중', color: '#4C9AFF', bg: 'rgba(76,154,255,0.15)' },
  completed: { label: '완료', color: '#51CF66', bg: 'rgba(81,207,102,0.15)' },
  failed: { label: '실패', color: '#E74C3C', bg: 'rgba(231,76,60,0.15)' },
  expired: { label: '만료', color: '#E8A33D', bg: 'rgba(232,163,61,0.15)' },
}

// 처리 PC 표시: 내 PC(트리거 device)와 일치하면 '이 PC', 아니면 device 끝 4자리.
function deviceTag(ownerDeviceId: string, myDeviceId: string): { label: string; mine: boolean } {
  if (!ownerDeviceId) return { label: '미배정', mine: false }
  if (myDeviceId && ownerDeviceId === myDeviceId) return { label: '이 PC', mine: true }
  return { label: `타 PC ${ownerDeviceId.slice(-4)}`, mine: false }
}

const SITE_LABEL: Record<string, string> = {
  MUSINSA: '무신사',
  ABCmart: 'ABC마트',
  SSG: 'SSG',
  GSShop: 'GS샵',
  LOTTEON: '롯데ON',
  NAVERSTORE: '네이버',
  KREAM: '크림',
}

function reviewKey(site: string): string {
  return ({
    MUSINSA: 'musinsa',
    ABCmart: 'abcmart',
    SSG: 'ssg',
    GSShop: 'gs',
    LOTTEON: 'lotteon',
    NAVERSTORE: 'naver',
    KREAM: 'kream',
  } as Record<string, string>)[site] || ''
}

function reviewAction(site: string): string {
  const k = reviewKey(site)
  return k ? `${k}_review` : ''
}

export default function RewardsPage() {
  const [data, setData] = useState<RewardsStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [intervalDraft, setIntervalDraft] = useState<number>(24)
  const [busy, setBusy] = useState<string | null>(null)
  const [msg, setMsg] = useState<string>('')
  const [jobs, setJobs] = useState<RewardJob[]>([])
  const [myDeviceId, setMyDeviceId] = useState<string>('')
  const [logMessages, setLogMessages] = useState<string[]>(['[대기] 적립 로그'])
  const prevJobStatusRef = useRef<Map<string, string>>(new Map())
  const logSeededRef = useRef(false)
  // 폴링 주기 결정용 — 최신 잡 목록을 인터벌 스케줄러에서 참조
  const jobsRef = useRef<RewardJob[]>([])

  const pushLog = useCallback((line: string) => {
    setLogMessages((p) => [...p, line].slice(-300))
  }, [])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await rewardsApi.status()
      setData(res)
      setIntervalDraft(res.auto_interval_hours || 0)
    } catch (e) {
      const err = e as Error
      setMsg(`상태 조회 실패: ${err.message}`)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
    const t = setInterval(() => {
      void load()
    }, 30000)
    return () => clearInterval(t)
  }, [load])

  // 잡 실행 상태 폴링. 적재→실행중→완료/실패 + 처리 PC 가시화.
  const loadJobs = useCallback(async () => {
    try {
      const res = await rewardsApi.jobStatus()
      const list = res.jobs || []
      jobsRef.current = list
      setJobs(list)
    } catch {
      // 상태 표시는 보조 기능 — 실패해도 본 화면에 영향 없음
    }
  }, [])

  // 적응형 폴링: 진행중(pending/dispatched) 잡 있으면 4s, 없으면 12s.
  // 상시 4s 폴링은 read 풀(소싱처계정+잡 조회)을 계속 점유 → 평상시 부하 절감.
  useEffect(() => {
    setMyDeviceId(getDeviceId())
    let stopped = false
    let timer: ReturnType<typeof setTimeout>
    const tick = async () => {
      await loadJobs()
      if (stopped) return
      const active = jobsRef.current.some(
        (j) => j.status === 'pending' || j.status === 'dispatched',
      )
      timer = setTimeout(tick, active ? 4000 : 12000)
    }
    void tick()
    return () => {
      stopped = true
      clearTimeout(timer)
    }
  }, [loadJobs])

  const grouped = useMemo(() => {
    const m = new Map<string, RewardAccountRow[]>()
    if (!data) return m
    for (const a of data.accounts) {
      if (!m.has(a.site_name)) m.set(a.site_name, [])
      m.get(a.site_name)!.push(a)
    }
    return m
  }, [data])

  // account_id → 최근 잡 (액션별 최신 1개). 백엔드가 created_at desc 정렬해서 내려줌.
  const jobsByAccount = useMemo(() => {
    const m = new Map<string, RewardJob[]>()
    const seen = new Set<string>()
    for (const j of jobs) {
      const key = `${j.account_id}|${j.action}`
      if (seen.has(key)) continue
      seen.add(key)
      const list = m.get(j.account_id)
      if (list) list.push(j)
      else m.set(j.account_id, [j])
    }
    return m
  }, [jobs])

  // 잡 상태 전이를 로그로 — 적재됨→실행중→완료/실패. 첫 로드는 seed만(과거 잡 스팸 방지).
  useEffect(() => {
    const acctLabel = new Map(
      (data?.accounts || []).map((a) => [a.id, a.account_label]),
    )
    const next = new Map<string, string>()
    const lines: string[] = []
    for (const j of jobs) {
      next.set(j.request_id, j.status)
      if (
        logSeededRef.current &&
        prevJobStatusRef.current.get(j.request_id) !== j.status
      ) {
        const acct = acctLabel.get(j.account_id) || (j.account_id || '').slice(-6) || '계정'
        const act = ACTION_LABEL[j.action] || j.action
        const st = JOB_STATUS_VIEW[j.status]?.label || j.status
        const dt = deviceTag(j.owner_device_id, myDeviceId)
        lines.push(`[적립] ${acct} ${act}: ${st} (${dt.label})`)
      }
    }
    prevJobStatusRef.current = next
    if (!logSeededRef.current) {
      logSeededRef.current = true
      return
    }
    if (lines.length) setLogMessages((p) => [...p, ...lines].slice(-300))
  }, [jobs, data, myDeviceId])

  const totalMusinsaMoney = useMemo(
    () =>
      (data?.accounts || [])
        .filter((a) => a.site_name === 'MUSINSA')
        .reduce((s, a) => s + (a.balance || 0), 0),
    [data],
  )

  const totalMusinsaMileage = useMemo(
    () =>
      (data?.accounts || [])
        .filter((a) => a.site_name === 'MUSINSA')
        .reduce((s, a) => s + (a.mileage || 0), 0),
    [data],
  )

  const handleRunAll = async () => {
    setBusy('all')
    setMsg('전체 실행 중...')
    pushLog('[적립] 지금 전체 실행 — 큐 적재 시작...')
    try {
      const r = await rewardsApi.runNow()
      const arr = (r.summary as Array<{ enqueued: unknown[] }>) || []
      const total = arr.reduce((s, x) => s + (x.enqueued?.length || 0), 0)
      setMsg(`전체 실행 — 잡 ${fmtNum(total)}건 적재`)
      pushLog(`[적립] 전체 실행 큐 적재 완료 — ${fmtNum(total)}건 (확장앱이 처리 중...)`)
      await load()
      void loadJobs()
    } catch (e) {
      setMsg(`실행 실패: ${(e as Error).message}`)
      pushLog(`[적립] 전체 실행 실패: ${(e as Error).message}`)
    } finally {
      setBusy(null)
    }
  }

  const handleRunAccount = async (accountId: string, action?: string) => {
    setBusy(`${accountId}:${action || 'all'}`)
    setMsg('실행 중...')
    const acctLabel =
      (data?.accounts || []).find((a) => a.id === accountId)?.account_label ||
      accountId.slice(-6)
    const actLabel = action ? ACTION_LABEL[action] || action : '전체'
    pushLog(`[적립] 계정 실행 — ${acctLabel}/${actLabel} 큐 적재 시작...`)
    try {
      const r = await rewardsApi.runAccount(accountId, action ? [action] : undefined)
      setMsg(`계정 실행 — 잡 ${fmtNum(r.enqueued.length)}건 적재`)
      pushLog(`[적립] ${acctLabel} 큐 적재 완료 — ${fmtNum(r.enqueued.length)}건 (확장앱이 처리 중...)`)
      await load()
      void loadJobs()
    } catch (e) {
      setMsg(`실행 실패: ${(e as Error).message}`)
      pushLog(`[적립] ${acctLabel} 실행 실패: ${(e as Error).message}`)
    } finally {
      setBusy(null)
    }
  }

  const handleSaveInterval = async () => {
    setBusy('interval')
    try {
      await rewardsApi.setAutoSettings(intervalDraft)
      setMsg(`자동 인터벌 ${fmtNum(intervalDraft)}시간 저장`)
      await load()
    } catch (e) {
      setMsg(`저장 실패: ${(e as Error).message}`)
    } finally {
      setBusy(null)
    }
  }

  return (
    <div style={{ padding: '1rem', maxWidth: '1400px', margin: '0 auto', color: '#E5E5E5' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: '0.75rem', marginBottom: '1rem' }}>
        <h1 style={{ fontSize: '1.4rem', fontWeight: 700, color: '#E5E5E5' }}>적립금</h1>
        <span style={{ fontSize: '0.85rem', color: '#888' }}>
          계정별 적립금 및 리뷰 현황을 24시간마다 자동으로 동기화합니다.
        </span>
      </div>

      {/* 상단 요약 + 자동 인터벌 */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: '1fr 1fr 1fr',
          gap: '0.75rem',
          marginBottom: '1rem',
        }}
      >
        <div style={cardStyle}>
          <div style={labelStyle}>무신사 머니 (전체 합계)</div>
          <div style={{ ...valueStyle, color: '#51CF66' }}>{fmtNum(Math.round(totalMusinsaMoney))}원</div>
        </div>
        <div style={cardStyle}>
          <div style={labelStyle}>무신사 적립금 (전체 합계)</div>
          <div style={{ ...valueStyle, color: '#4C9AFF' }}>{fmtNum(Math.round(totalMusinsaMileage))}원</div>
        </div>
        <div style={cardStyle}>
          <div style={labelStyle}>활성 계정</div>
          <div style={valueStyle}>{fmtNum(data?.accounts.length ?? 0)}개</div>
        </div>
      </div>

      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '0.75rem',
          marginBottom: '1rem',
          padding: '0.7rem 0.9rem',
          background: 'rgba(30,30,30,0.5)',
          backdropFilter: 'blur(20px)',
          borderRadius: '8px',
          border: '1px solid #2D2D2D',
          flexWrap: 'wrap',
        }}
      >
        <span style={{ fontSize: '0.85rem', fontWeight: 600, color: '#E5E5E5' }}>자동 실행:</span>
        <input
          type="number"
          min={0}
          max={168}
          value={intervalDraft}
          onChange={(e) => setIntervalDraft(Number(e.target.value) || 0)}
          style={{
            width: '70px',
            padding: '0.3rem 0.5rem',
            fontSize: '0.85rem',
            background: '#1A1A1A',
            border: '1px solid #2D2D2D',
            borderRadius: '4px',
            color: '#E5E5E5',
            outline: 'none',
          }}
        />
        <span style={{ fontSize: '0.85rem', color: '#A0A0A0' }}>시간마다 (0 = 비활성)</span>
        <button
          onClick={handleSaveInterval}
          disabled={busy === 'interval'}
          style={btnStylePrimary}
        >
          {busy === 'interval' ? '저장 중' : '저장'}
        </button>
        <span style={{ fontSize: '0.8rem', color: '#888' }}>
          마지막 자동 실행: {data?.last_auto_run_at ? formatRelative(data.last_auto_run_at) : '-'}
        </span>
        <div style={{ flex: 1 }} />
        <button onClick={handleRunAll} disabled={busy === 'all'} style={btnStylePrimary}>
          {busy === 'all' ? '실행 중' : '지금 전체 실행'}
        </button>
      </div>

      {msg && (
        <div
          style={{
            padding: '0.5rem 0.8rem',
            background: 'rgba(255,68,68,0.1)',
            border: '1px solid rgba(255,68,68,0.3)',
            borderRadius: '4px',
            fontSize: '0.85rem',
            marginBottom: '0.75rem',
            color: '#FFB3B3',
          }}
        >
          {msg}
        </div>
      )}

      {/* 적립 로그 — 주문 송장 로그와 동일 패턴: 액션 + 잡 상태전이(적재→실행중→완료/실패 + 처리 PC) */}
      <div style={{ border: '1px solid #1C2333', borderRadius: '8px', overflow: 'hidden', marginBottom: '0.75rem' }}>
        <div style={{ padding: '6px 14px', background: '#0D1117', borderBottom: '1px solid #1C2333', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <span style={{ fontSize: '0.8rem', fontWeight: 600, color: '#94A3B8' }}>적립 로그</span>
          <div style={{ display: 'flex', gap: '4px' }}>
            <button onClick={() => { void navigator.clipboard?.writeText(logMessages.join('\n')).catch(() => {}) }} style={logBtnStyle}>복사</button>
            <button onClick={() => setLogMessages(['[대기] 로그가 초기화되었습니다.'])} style={logBtnStyle}>초기화</button>
          </div>
        </div>
        <div
          ref={(el) => {
            if (el) el.scrollTop = el.scrollHeight
          }}
          style={{ height: '144px', overflowY: 'auto', padding: '8px 14px', fontFamily: "'Courier New', monospace", fontSize: '0.788rem', color: '#8A95B0', background: '#080A10', lineHeight: 1.8 }}
        >
          {logMessages.map((m, i) => (
            <p key={i} style={{ color: '#8A95B0', margin: 0 }}>
              {m}
            </p>
          ))}
        </div>
      </div>

      {loading && !data ? (
        <div style={{ color: '#888', fontSize: '0.9rem' }}>불러오는 중...</div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          {Array.from(grouped.entries()).map(([site, accounts]) => (
            <div
              key={site}
              style={{
                border: '1px solid #2D2D2D',
                borderRadius: '8px',
                overflow: 'hidden',
                background: 'rgba(30,30,30,0.5)',
                backdropFilter: 'blur(20px)',
              }}
            >
              <div
                style={{
                  padding: '0.65rem 0.9rem',
                  background: 'rgba(20,20,20,0.6)',
                  borderBottom: '1px solid #2D2D2D',
                  fontWeight: 700,
                  fontSize: '0.95rem',
                  color: '#E5E5E5',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '0.5rem',
                }}
              >
                <span>{SITE_LABEL[site] || site}</span>
                <span style={{ color: '#888', fontWeight: 400, fontSize: '0.8rem' }}>
                  {fmtNum(accounts.length)}개 계정
                </span>
              </div>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85rem' }}>
                <thead>
                  <tr>
                    <th style={thStyle}>계정</th>
                    {site === 'MUSINSA' && (
                      <>
                        <th style={thStyle}>머니</th>
                        <th style={thStyle}>적립금</th>
                        <th style={thStyle}>출석 (연속/마지막)</th>
                        <th style={thStyle}>스냅 (마지막)</th>
                      </>
                    )}
                    {site === 'ABCmart' && (
                      <>
                        <th style={thStyle}>스탬프</th>
                        <th style={thStyle}>점수</th>
                        <th style={thStyle}>출석 (마지막)</th>
                      </>
                    )}
                    <th style={thStyle}>리뷰 (누적/최근)</th>
                    <th style={thStyle}>상태</th>
                    <th style={{ ...thStyle, textAlign: 'right' }}>작업</th>
                  </tr>
                </thead>
                <tbody>
                  {accounts.map((a) => (
                    <AccountRow
                      key={a.id}
                      a={a}
                      busy={busy}
                      onRun={handleRunAccount}
                      jobs={jobsByAccount.get(a.id)}
                      myDeviceId={myDeviceId}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          ))}
          {grouped.size === 0 && (
            <div
              style={{
                color: '#888',
                fontSize: '0.9rem',
                padding: '1.5rem',
                textAlign: 'center',
                background: 'rgba(30,30,30,0.5)',
                border: '1px solid #2D2D2D',
                borderRadius: '8px',
              }}
            >
              활성 소싱처 계정이 없습니다. 설정 페이지에서 무신사/ABC마트/SSG/GS샵/롯데ON/네이버/크림 계정을 추가하세요.
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function AccountRow({
  a,
  busy,
  onRun,
  jobs,
  myDeviceId,
}: {
  a: RewardAccountRow
  busy: string | null
  onRun: (accountId: string, action?: string) => void
  jobs?: RewardJob[]
  myDeviceId: string
}) {
  const isMusinsa = a.site_name === 'MUSINSA'
  const isAbc = a.site_name === 'ABCmart'
  const attendanceFresh = isFresh24h(a.last_musinsa_attendance_at)
  const snapFresh = isFresh24h(a.last_musinsa_snap_like_at)
  const abcFresh = isFresh24h(a.last_abcmart_attendance_at)

  return (
    <tr style={{ borderBottom: '1px solid #2D2D2D' }}>
      <td style={tdStyle}>
        <div style={{ fontWeight: 600, color: '#E5E5E5' }}>{a.account_label}</div>
        <div style={{ color: '#888', fontSize: '0.75rem' }}>{a.username}</div>
        {jobs && jobs.length > 0 && (
          <div
            style={{
              marginTop: '0.35rem',
              display: 'flex',
              flexDirection: 'column',
              gap: '0.2rem',
            }}
          >
            {jobs.map((j) => {
              const v = JOB_STATUS_VIEW[j.status] || JOB_STATUS_VIEW.pending
              const dt = deviceTag(j.owner_device_id, myDeviceId)
              return (
                <div
                  key={j.request_id}
                  style={{ display: 'flex', alignItems: 'center', gap: '0.3rem', fontSize: '0.7rem' }}
                >
                  <span style={{ color: '#999' }}>{ACTION_LABEL[j.action] || j.action}</span>
                  <span
                    style={{
                      padding: '0.05rem 0.35rem',
                      borderRadius: '3px',
                      fontWeight: 600,
                      color: v.color,
                      background: v.bg,
                    }}
                  >
                    {v.label}
                  </span>
                  <span style={{ color: dt.mine ? '#51CF66' : '#888' }}>{dt.label}</span>
                  {j.error && (
                    <span title={j.error} style={{ color: '#E74C3C', cursor: 'help' }}>
                      ⓘ
                    </span>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </td>

      {isMusinsa && (
        <>
          <td style={tdStyle}>
            <span style={{ color: '#51CF66', fontWeight: 600 }}>
              {fmtNum(Math.round(a.balance ?? 0))}원
            </span>
          </td>
          <td style={tdStyle}>
            <span style={{ color: '#4C9AFF', fontWeight: 600 }}>
              {fmtNum(Math.round(a.mileage ?? 0))}원
            </span>
          </td>
          <td style={tdStyle}>
            <div>
              {a.musinsa_attendance_streak ? `${fmtNum(a.musinsa_attendance_streak)}일 연속` : '-'}
            </div>
            <div style={{ color: attendanceFresh ? '#51CF66' : '#666', fontSize: '0.75rem' }}>
              {formatRelative(a.last_musinsa_attendance_at)}
            </div>
          </td>
          <td style={tdStyle}>
            <div>
              {a.last_musinsa_snap_reward ? `${fmtNum(a.last_musinsa_snap_reward)}원` : '-'}
            </div>
            <div style={{ color: snapFresh ? '#51CF66' : '#666', fontSize: '0.75rem' }}>
              {formatRelative(a.last_musinsa_snap_like_at)}
            </div>
          </td>
        </>
      )}

      {isAbc && (
        <>
          <td style={tdStyle}>{fmtNum(a.abcmart_stamp_count ?? 0)}개</td>
          <td style={tdStyle}>{fmtNum(a.abcmart_stamp_score ?? 0)}</td>
          <td style={tdStyle}>
            <div style={{ color: abcFresh ? '#51CF66' : '#666', fontSize: '0.75rem' }}>
              {formatRelative(a.last_abcmart_attendance_at)}
            </div>
          </td>
        </>
      )}

      {/* 리뷰 컬럼 — 사이트 키 기반으로 누적/최근값 표시 */}
      {(() => {
        const k = reviewKey(a.site_name)
        const total = (a as unknown as Record<string, number | null>)[`${k}_review_total`] ?? 0
        const last = (a as unknown as Record<string, number | null>)[`last_${k}_review_count`] ?? 0
        const at = (a as unknown as Record<string, string | null>)[`last_${k}_review_at`]
        const fresh = isFresh24h(at)
        return (
          <td style={tdStyle}>
            <div>
              {fmtNum(Number(total))}건 누적 {last ? `(+${fmtNum(Number(last))})` : ''}
            </div>
            <div style={{ color: fresh ? '#51CF66' : '#666', fontSize: '0.75rem' }}>
              {formatRelative(at)}
            </div>
          </td>
        )
      })()}

      <td style={tdStyle}>
        {a.cookie_expired ? (
          <span style={{ color: '#E74C3C', fontWeight: 600 }}>쿠키 만료</span>
        ) : a.is_login_default ? (
          <span style={{ color: '#51CF66' }}>기본계정</span>
        ) : (
          <span style={{ color: '#888' }}>활성</span>
        )}
      </td>

      <td style={{ ...tdStyle, textAlign: 'right' }}>
        <div style={{ display: 'flex', gap: '0.3rem', justifyContent: 'flex-end', flexWrap: 'wrap' }}>
          {isMusinsa && (
            <>
              <button
                onClick={() => onRun(a.id, 'musinsa_attendance')}
                disabled={!!busy}
                style={btnStyleSmall}
                title={ACTION_LABEL.musinsa_attendance}
              >
                출석
              </button>
              <button
                onClick={() => onRun(a.id, 'musinsa_snap_like')}
                disabled={!!busy}
                style={btnStyleSmall}
                title={ACTION_LABEL.musinsa_snap_like}
              >
                스냅
              </button>
              <button
                onClick={() => onRun(a.id, 'musinsa_balance')}
                disabled={!!busy}
                style={btnStyleSmall}
                title={ACTION_LABEL.musinsa_balance}
              >
                잔액
              </button>
            </>
          )}
          {isAbc && (
            <button
              onClick={() => onRun(a.id, 'abcmart_attendance')}
              disabled={!!busy}
              style={btnStyleSmall}
              title={ACTION_LABEL.abcmart_attendance}
            >
              출석
            </button>
          )}
          {(() => {
            const act = reviewAction(a.site_name)
            return act ? (
              <button
                onClick={() => onRun(a.id, act)}
                disabled={!!busy}
                style={btnStyleSmall}
                title="리뷰 자동작성"
              >
                리뷰
              </button>
            ) : null
          })()}
          <button onClick={() => onRun(a.id)} disabled={!!busy} style={btnStyleSmallPrimary}>
            전체
          </button>
        </div>
      </td>
    </tr>
  )
}

const logBtnStyle: React.CSSProperties = {
  fontSize: '0.72rem',
  color: '#555',
  background: 'transparent',
  border: '1px solid #1C2333',
  padding: '1px 8px',
  borderRadius: '4px',
  cursor: 'pointer',
}

const cardStyle: React.CSSProperties = {
  padding: '0.75rem 1rem',
  background: 'rgba(30,30,30,0.5)',
  backdropFilter: 'blur(20px)',
  border: '1px solid #2D2D2D',
  borderRadius: '8px',
  color: '#E5E5E5',
}

const labelStyle: React.CSSProperties = {
  fontSize: '0.75rem',
  color: '#888',
  marginBottom: '0.25rem',
}

const valueStyle: React.CSSProperties = {
  fontSize: '1.2rem',
  fontWeight: 700,
  color: '#E5E5E5',
}

const thStyle: React.CSSProperties = {
  padding: '0.5rem 0.75rem',
  textAlign: 'left',
  fontWeight: 600,
  color: '#A0A0A0',
  fontSize: '0.78rem',
  borderBottom: '1px solid #2D2D2D',
  background: 'rgba(20,20,20,0.6)',
}

const tdStyle: React.CSSProperties = {
  padding: '0.5rem 0.75rem',
  verticalAlign: 'top',
  color: '#E5E5E5',
  fontSize: '0.85rem',
}

const btnStylePrimary: React.CSSProperties = {
  padding: '0.35rem 0.7rem',
  fontSize: '0.8rem',
  background: '#FF4444',
  color: '#fff',
  border: 'none',
  borderRadius: '4px',
  cursor: 'pointer',
  fontWeight: 600,
}

const btnStyleSmall: React.CSSProperties = {
  padding: '0.2rem 0.5rem',
  fontSize: '0.75rem',
  background: '#1A1A1A',
  color: '#E5E5E5',
  border: '1px solid #2D2D2D',
  borderRadius: '3px',
  cursor: 'pointer',
}

const btnStyleSmallPrimary: React.CSSProperties = {
  padding: '0.2rem 0.55rem',
  fontSize: '0.75rem',
  background: '#FF4444',
  color: '#fff',
  border: 'none',
  borderRadius: '3px',
  cursor: 'pointer',
  fontWeight: 600,
}
