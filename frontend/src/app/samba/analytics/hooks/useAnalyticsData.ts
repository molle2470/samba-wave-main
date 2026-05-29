'use client'

import { useState, useCallback, useEffect, useRef } from 'react'
import {
  accountApi, collectorApi, orderApi,
  type SambaMarketAccount, type AnalyticsAggregateRow,
} from '@/lib/samba/api/commerce'
import {
  analyticsApi,
  type SourcingRoi, type ProductPerformance, type BrandSales,
} from '@/lib/samba/api/operations'
import { SOURCE_SITES } from '../constants'

interface Args {
  searchYear: number
  searchMonth: number
  selectedMarkets: string[]
  selectedSites: string[]
  selectedStatuses: string[]
  setSelectedSites: (v: string[]) => void
  setSelectedMarkets: (v: string[]) => void
  hasStoredMarkets: boolean
  hasStoredSites: boolean
}

export function useAnalyticsData({
  searchYear, searchMonth,
  selectedMarkets, selectedSites, selectedStatuses,
  setSelectedSites, setSelectedMarkets,
  hasStoredMarkets, hasStoredSites,
}: Args) {
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [marketAccounts, setMarketAccounts] = useState<SambaMarketAccount[]>([])
  const [aggregate, setAggregate] = useState<AnalyticsAggregateRow[]>([])
  const [, setChannelData] = useState<{ channel_name: string; sales: number; orders: number; profit: number }[]>([])
  const [dailyData, setDailyData] = useState<{ date: string; sales: number; orders: number; profit: number }[]>([])
  const [sourcingRoi, setSourcingRoi] = useState<SourcingRoi[]>([])
  const [bestSellers, setBestSellers] = useState<ProductPerformance[]>([])
  const [brandData, setBrandData] = useState<BrandSales[]>([])

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const start = searchMonth > 0
        ? `${searchYear}-${String(searchMonth).padStart(2, '0')}-01`
        : `${searchYear}-01-01`
      const end = searchMonth > 0
        ? `${searchYear}-${String(searchMonth).padStart(2, '0')}-${new Date(searchYear, searchMonth, 0).getDate()}`
        : `${searchYear}-12-31`

      // 사전집계 엔드포인트 — 실패 시 사용자에게 에러 노출(silent [] 회귀 차단)
      let rows: AnalyticsAggregateRow[] = []
      try {
        const resp = await orderApi.analyticsAggregate(start, end)
        rows = resp.rows || []
      } catch (e) {
        setError(e instanceof Error ? e.message : '매출 데이터 조회 실패')
        rows = []
      }
      setAggregate(rows)

      const mkts = selectedMarkets.length > 0 ? selectedMarkets : undefined
      const sts = selectedSites.length > 0 ? selectedSites : undefined
      const stats = selectedStatuses.length > 0 ? selectedStatuses : undefined
      const [ch, daily, roi, best, brands] = await Promise.all([
        analyticsApi.channels().catch(() => []),
        analyticsApi.daily(30).catch(() => []),
        analyticsApi.sourcingRoi(start, end).catch(() => []),
        analyticsApi.bestSellers(10, 30, mkts, sts, stats).catch(() => []),
        analyticsApi.brands(start, end, mkts, sts, stats).catch(() => []),
      ])
      setChannelData(ch)
      setDailyData(daily)
      setSourcingRoi(roi)
      setBestSellers(best)
      setBrandData(brands)
    } finally {
      setLoading(false)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchYear, searchMonth, selectedMarkets, selectedSites, selectedStatuses])

  useEffect(() => { load() }, [load])

  useEffect(() => {
    const init = async () => {
      const accounts = await accountApi.listActive().catch(() => [] as SambaMarketAccount[])
      setMarketAccounts(accounts)
      // 소싱사이트 기본값 — localStorage 저장값 없을 때만 채움(사용자 선택 덮어쓰기 금지)
      if (!hasStoredSites) {
        const allData = await collectorApi.scrollProducts({ limit: 1 }).catch(() => null)
        if (allData) {
          const collectedSites = (allData.sites || []).filter((s: string) => SOURCE_SITES.includes(s))
          if (collectedSites.length > 0) setSelectedSites(collectedSites)
        }
      }
    }
    init()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 마켓 기본값: aggregate에서 채널명 추출
  // - 저장값 없으면 전체 초기화
  // - 저장값 있어도 aggregate에 새 마켓이 있으면 자동 추가 (신규 마켓 누락 방지)
  const initialMarketSet = useRef(false)
  useEffect(() => {
    if (!initialMarketSet.current && aggregate.length > 0) {
      initialMarketSet.current = true
      const orderMarkets = new Set<string>()
      for (const r of aggregate) {
        if (r.channel_name) {
          const name = r.channel_name
          const idx = name.indexOf('(')
          const market = (idx > 0 ? name.substring(0, idx) : name).trim()
          if (market) orderMarkets.add(market)
        }
      }
      if (orderMarkets.size === 0) return
      if (!hasStoredMarkets) {
        setSelectedMarkets([...orderMarkets])
      } else {
        // 기존 선택에 없는 신규 마켓 자동 추가
        const toAdd = [...orderMarkets].filter(m => !selectedMarkets.includes(m))
        if (toAdd.length > 0) setSelectedMarkets([...selectedMarkets, ...toAdd])
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [aggregate])

  return {
    loading, error, marketAccounts, aggregate,
    bestSellers, brandData,
    load,
  }
}
