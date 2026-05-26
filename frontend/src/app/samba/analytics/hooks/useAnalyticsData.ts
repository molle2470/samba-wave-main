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
  setSelectedSites: (v: string[]) => void
  setSelectedMarkets: (v: string[]) => void
  hasStoredMarkets: boolean
  hasStoredSites: boolean
}

export function useAnalyticsData({
  searchYear, searchMonth, setSelectedSites, setSelectedMarkets,
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

      const [ch, daily, roi, best, brands] = await Promise.all([
        analyticsApi.channels().catch(() => []),
        analyticsApi.daily(30).catch(() => []),
        analyticsApi.sourcingRoi(start, end).catch(() => []),
        analyticsApi.bestSellers(10, 30).catch(() => []),
        analyticsApi.brands(start, end).catch(() => []),
      ])
      setChannelData(ch)
      setDailyData(daily)
      setSourcingRoi(roi)
      setBestSellers(best)
      setBrandData(brands)
    } finally {
      setLoading(false)
    }
  }, [searchYear, searchMonth])

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

  // 마켓 기본값: aggregate에서 채널명 추출 — localStorage 저장값 없을 때만 1회
  const initialMarketSet = useRef(false)
  useEffect(() => {
    if (!initialMarketSet.current && aggregate.length > 0 && !hasStoredMarkets) {
      initialMarketSet.current = true
      const orderMarkets = new Set<string>()
      for (const r of aggregate) {
        if (r.channel_name) {
          const name = r.channel_name
          const idx = name.indexOf('(')
          orderMarkets.add(idx > 0 ? name.substring(0, idx) : name)
        }
      }
      if (orderMarkets.size > 0) setSelectedMarkets([...orderMarkets])
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [aggregate])

  return {
    loading, error, marketAccounts, aggregate,
    dailyData, sourcingRoi, bestSellers, brandData,
    load,
  }
}
