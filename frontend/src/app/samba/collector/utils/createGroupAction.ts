'use client'

import { Dispatch, SetStateAction } from 'react'
import { collectorApi, proxyApi } from '@/lib/samba/api/commerce'
import { fetchWithAuth, API_BASE } from '@/lib/samba/api/shared'
import { showAlert } from '@/components/samba/Modal'
import { fmtNum } from '@/lib/samba/styles'

const FIXED_REQUESTED_COUNT = 1000

export interface CreateGroupArgs {
  brandCode?: string
  collectUrl: string
  selectedSite: string
  checkedOptions: Record<string, boolean>
  setCollecting: Dispatch<SetStateAction<boolean>>
  setCollectUrl: Dispatch<SetStateAction<string>>
  addLog: (msg: string) => void
  load: () => void | Promise<void>
  loadTree: () => void | Promise<void>
}

export async function performCreateGroup(args: CreateGroupArgs) {
  const {
    brandCode, collectUrl, selectedSite, checkedOptions,
    setCollecting, setCollectUrl, addLog, load, loadTree,
  } = args
  const input = collectUrl.trim()
  if (!input) return
  setCollecting(true)
  addLog(`그룹 생성 중: ${input}${brandCode ? ` (브랜드: ${brandCode})` : ''}`)
  try {
    const site = selectedSite
    try {
      const host = new URL(input).hostname
      const siteHostMap: Record<string, string[]> = {
        MUSINSA: ['musinsa.com'], KREAM: ['kream.co.kr'], FashionPlus: ['fashionplus.co.kr'],
        Nike: ['nike.com'], Adidas: ['adidas.co.kr', 'adidas.com'],
        ABCmart: ['a-rt.com'], REXMONDE: ['okmall.com'],
        LOTTEON: ['lotteon.com'], GSShop: ['gsshop.com'], ElandMall: ['elandmall.com'],
        SSF: ['ssfshop.com'], SSG: ['ssg.com'],
      }
      const allowedHosts = siteHostMap[site] || []
      if (allowedHosts.length > 0 && !allowedHosts.some(h => host.includes(h))) {
        showAlert(`선택한 소싱처(${site})와 URL 도메인(${host})이 일치하지 않습니다`, 'error')
        setCollecting(false)
        return
      }
    } catch { /* URL이 아닌 경우 검증 스킵 */ }

    let keyword = ""
    let isUrl = false
    try {
      const parsed = new URL(input)
      isUrl = true
      keyword = parsed.searchParams.get("keyword")
        || parsed.searchParams.get("searchWord")
        || parsed.searchParams.get("q")
        || parsed.searchParams.get("query")
        || parsed.searchParams.get("kwd")
        || parsed.searchParams.get("tq")
        || parsed.searchParams.get("tab")
        || ""
    } catch {
      keyword = input
    }

    let groupName = keyword ? `${site}_${keyword.replace(/\s+/g, '_')}` : `${site}_${new Date().toLocaleDateString("ko-KR")}`

    // SNKRDUNK: URL 경로 세그먼트를 카테고리명으로 사용 (예: /en/trading-cards → trading-cards)
    if (site === 'SNKRDUNK' && isUrl && !keyword) {
      try {
        const parsed = new URL(input)
        const segments = parsed.pathname.split('/').filter(s => s && s !== 'en')
        const category = segments.join('_')
        if (category) {
          groupName = `SNKRDUNK_${category}`
        }
      } catch { /* 파싱 실패 시 기존 groupName 유지 */ }
    }

    if (site === 'NAVERSTORE' && isUrl && (input.includes('smartstore.naver.com') || input.includes('brand.naver.com'))) {
      try {
        const infoRes = await fetchWithAuth(
          `${API_BASE}/api/v1/samba/naverstore-sourcing/url-info?store_url=${encodeURIComponent(input)}`
        )
        if (infoRes.ok) {
          const info = await infoRes.json()
          const storeName = (info.storeName || '').trim()
          const categoryName = (info.categoryName || '전체상품').trim()
          if (storeName) {
            groupName = `NAVERSTORE_${storeName}_${categoryName.replace(/\s+/g, '_')}`
          }
        }
      } catch { /* url-info 실패 시 기존 groupName 유지 */ }
    }

    let keywordUrl = input
    if (site === "MUSINSA") {
      let u: URL
      if (!isUrl) {
        u = new URL("https://www.musinsa.com/search/goods")
        u.searchParams.set("keyword", keyword)
      } else {
        try { u = new URL(input) } catch { u = new URL("https://www.musinsa.com/search/goods"); u.searchParams.set("keyword", keyword) }
      }
      if (brandCode) u.searchParams.set("brand", brandCode)
      if (checkedOptions['excludePreorder']) u.searchParams.set("excludePreorder", "1")
      if (checkedOptions['excludeBoutique']) u.searchParams.set("excludeBoutique", "1")
      if (checkedOptions['maxDiscount']) u.searchParams.set("maxDiscount", "1")
      if (checkedOptions['includeSoldOut']) u.searchParams.set("includeSoldOut", "1")
      keywordUrl = u.toString()
    }
    if (site === 'FashionPlus' && !isUrl) {
      const u = new URL('https://www.fashionplus.co.kr/search/goods/result')
      u.searchParams.set('searchWord', keyword)
      if (checkedOptions['skipDetail']) u.searchParams.set('skipDetail', '1')
      keywordUrl = u.toString()
    } else if (checkedOptions['skipDetail'] && keywordUrl.startsWith('http')) {
      const u = new URL(keywordUrl)
      u.searchParams.set('skipDetail', '1')
      keywordUrl = u.toString()
    }
    if (checkedOptions['maxDiscount'] && site !== 'MUSINSA' && keywordUrl.startsWith('http')) {
      const u = new URL(keywordUrl)
      u.searchParams.set('maxDiscount', '1')
      keywordUrl = u.toString()
    }
    if (checkedOptions['includeSoldOut'] && site !== 'MUSINSA' && keywordUrl.startsWith('http')) {
      const u = new URL(keywordUrl)
      u.searchParams.set('includeSoldOut', '1')
      keywordUrl = u.toString()
    }

    const requestedCount = FIXED_REQUESTED_COUNT
    try {
      const countResult = await proxyApi.searchCount(site, keyword, keywordUrl)
      if (countResult.totalCount > 0) {
        void countResult.totalCount
        addLog(`검색 결과: ${fmtNum(requestedCount)}개 상품`)
      }
    } catch { /* 조회 실패 시 기본값 유지 */ }

    const created = await collectorApi.createFilter({
      source_site: site,
      name: groupName,
      keyword: keywordUrl,
      requested_count: requestedCount,
    })

    addLog(`그룹 생성 완료: "${created.name}" (${site}, ${fmtNum(requestedCount)}개)`)
    setCollectUrl("")
    load(); loadTree()
  } catch (e) {
    addLog(`그룹 생성 실패: ${e instanceof Error ? e.message : "오류"}`)
  }
  setCollecting(false)
}
