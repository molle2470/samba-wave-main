"use client";

import React, { useEffect, useState, useCallback, useMemo, useRef } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import {
  collectorApi,
  accountApi,
  shipmentApi,
  proxyApi,
  type SambaCollectedProduct,
  type SambaPolicy,
  type SambaSearchFilter,
  type SambaMarketAccount,
  type RefreshDetail,
} from "@/lib/samba/api/commerce";
import { fetchWithAuth } from "@/lib/samba/api/shared";
import { type SambaNameRule, type SambaDetailTemplate } from "@/lib/samba/api/support";
import { showAlert, showConfirm } from '@/components/samba/Modal'
import { fmtNum as fmt, fmtTextNumbers } from '@/lib/samba/styles'
import { fmtTime } from '@/lib/samba/utils'
import ProductCard from './components/ProductCard'
import ProductImage from './components/ProductImage'
import { MARKETS } from './components/ProductCard'

type MarketDeleteModalState = {
  mode: 'single' | 'bulk'
  deleteMode: 'market' | 'force'
  title: string
  products: SambaCollectedProduct[]
  options: { accountId: string; label: string; marketType: string; productCount: number }[]
  selectedAccountIds: string[]
}

export default function ProductsPage() {
  useEffect(() => { document.title = 'SAMBA-상품관리' }, [])

  // 무신사 자동로그인계정 상태 — 60s 폴링. 미설정/만료 시 모달 경고.
  const [musinsaAuthMissing, setMusinsaAuthMissing] = useState<{
    reason: 'unset' | 'cookie_expired' | 'no_cookie'
    account_label: string | null
  } | null>(null)
  const [musinsaAuthDismissed, setMusinsaAuthDismissed] = useState<boolean>(false)
  useEffect(() => {
    let cancelled = false
    const apiBase = process.env.NEXT_PUBLIC_API_URL || 'https://api.samba-wave.co.kr'
    const tick = async () => {
      try {
        const r = await fetchWithAuth(`${apiBase}/api/v1/samba/sourcing-accounts/musinsa/autologin-status`)
        if (!r.ok) return
        const j = await r.json()
        if (cancelled) return
        if (j?.missing) {
          setMusinsaAuthMissing({ reason: j.reason, account_label: j.account_label })
        } else {
          setMusinsaAuthMissing(null)
          setMusinsaAuthDismissed(false)
        }
      } catch { /* ignore */ }
    }
    tick()
    const t = setInterval(tick, 60_000)
    return () => { cancelled = true; clearInterval(t) }
  }, [])
  const searchParams = useSearchParams();
  const router = useRouter();
  const [queryReady, setQueryReady] = useState(false)
  // URL searchParams에서 필터 읽기 — 한 번 읽은 뒤 URL에서 제거 (새로고침 시 풀림)
  // searchParams를 dep에 포함해야 클라이언트 네비게이션 시에도 동작함
  const [filterByGroupId, setFilterByGroupId] = useState(searchParams.get("search_filter_id") || "")
  const [filterGroupName, setFilterGroupName] = useState(searchParams.get("group_name") || "")
  useEffect(() => {
    const gid = searchParams.get("search_filter_id") || ""
    const gname = searchParams.get("group_name") || ""
    if (gid) {
      setFilterByGroupId(gid)
      setFilterGroupName(gname)
      setAppliedFilterByGroupId(gid)
      // URL에서 그룹 필터 파라미터 제거 (새로고침 시 풀리도록)
      // router.replace 대신 window.history.replaceState 사용 — Next.js 리내비게이션 방지
      // router.replace는 Next.js 내비게이션을 트리거해 컴포넌트 리마운트 → filterByGroupId 초기화 버그 유발
      const params = new URLSearchParams(window.location.search)
      params.delete("search_filter_id")
      params.delete("group_name")
      const qs = params.toString()
      window.history.replaceState(null, '', `/samba/products${qs ? `?${qs}` : ""}`)
    }
    setQueryReady(true)
  }, [searchParams])

  // highlight는 로컬 state로 관리 → 새로고침 시 자동 해제
  const [highlightProductId, setHighlightProductId] = useState(searchParams.get("highlight") || "");
  useEffect(() => {
    const h = searchParams.get("highlight")
    if (h) {
      setHighlightProductId(h)
      // URL에서 highlight 파라미터 제거 (뒤로가기 히스토리 안 남김)
      const params = new URLSearchParams(window.location.search)
      params.delete("highlight")
      const qs = params.toString()
      window.history.replaceState(null, '', `/samba/products${qs ? `?${qs}` : ""}`)
    }
  }, [searchParams]);

  const [allProducts, setAllProducts] = useState<SambaCollectedProduct[]>([]);
  const [policies, setPolicies] = useState<SambaPolicy[]>([]);
  const [accounts, setAccounts] = useState<SambaMarketAccount[]>([]);
  const accountsMap = useMemo(() => new Map(accounts.map(a => [a.id, a])), [accounts])
  const [detailTemplates, setDetailTemplates] = useState<SambaDetailTemplate[]>([]);
  const [filterNameMap, setFilterNameMap] = useState<Record<string, string>>({});
  const [searchFilters, setSearchFilters] = useState<SambaSearchFilter[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  // 서버사이드 페이지네이션 상태
  const [serverTotal, setServerTotal] = useState(0);
  const [serverSites, setServerSites] = useState<string[]>([]);

  // Filters
  const _initSearchType = searchParams.get("search_type") || "name";
  const _initSearch = searchParams.get("search") || "";
  // ID 검색은 내부 필터용 — 검색창에 표시하지 않음
  // highlight 파라미터가 있으면 해당 상품 ID로 검색
  const _highlightInit = searchParams.get("highlight") || ""
  const [_idFilter] = useState(
    _initSearchType === "id" ? _initSearch : (_highlightInit || "")
  );
  const [searchType, setSearchType] = useState(_initSearchType === "id" ? "name" : _initSearchType);
  const [searchQ, setSearchQ] = useState(_initSearchType === "id" ? "" : _initSearch);
  const [siteFilter, setSiteFilter] = useState("");
  const [soldOutFilter, setSoldOutFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [aiFilter, setAiFilter] = useState("");
  const [sortBy, setSortBy] = useState("collect-desc");
  const [appliedSearchType, setAppliedSearchType] = useState(_initSearchType === "id" ? "name" : _initSearchType);
  const [appliedSearchQ, setAppliedSearchQ] = useState(_initSearchType === "id" ? "" : _initSearch);
  const [appliedSiteFilter, setAppliedSiteFilter] = useState("");
  const [appliedSoldOutFilter, setAppliedSoldOutFilter] = useState("");
  const [appliedStatusFilter, setAppliedStatusFilter] = useState("");
  const [appliedAiFilter, setAppliedAiFilter] = useState("");
  const [appliedSortBy, setAppliedSortBy] = useState("collect-desc");
  const [appliedFilterByGroupId, setAppliedFilterByGroupId] = useState(searchParams.get("search_filter_id") || "")
  const [pageSize, setPageSize] = useState(20);
  const [currentPage, setCurrentPage] = useState(1);
  const [viewMode, setViewMode] = useState<"card" | "compact" | "image">("card");
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());
  // Selection
  const [selectAll, setSelectAll] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());

  // 상품별 로그 (업데이트 버튼 클릭 시 해당 상품 위에 표시)
  const [activeLog, setActiveLog] = useState<{ productId: string; message: string } | null>(null);
  // 작업 로그 (영상생성/이미지생성/태그생성 등)
  const [taskLogs, setTaskLogs] = useState<string[]>([]);
  const addTaskLog = (msg: string) => {
    const ts = fmtTime()
    setTaskLogs(prev => [...prev, `[${ts}] ${msg}`])
  }
  // AI 비용 추적
  const [lastAiUsage, setLastAiUsage] = useState<{ calls: number; tokens: number; cost: number; date: string } | null>(null);

  // AI 이미지 변환
  const [aiImgMode, setAiImgMode] = useState('background')
  const [aiModelPreset, setAiModelPreset] = useState('auto')
  const [aiPresetList, setAiPresetList] = useState<{ key: string; label: string; desc: string; image: string | null }[]>([])
  const [aiImgScope, setAiImgScope] = useState({ thumbnail: true, additional: true, detail: false })
  const [aiImgTransforming, setAiImgTransforming] = useState(false)
  const [imgFiltering, setImgFiltering] = useState(false)
  const [imgFilterScopes, setImgFilterScopes] = useState<Set<string>>(new Set(['detail_images']))

  // 유령삭제 마켓 선택 모달
  const [ghostChoiceModal, setGhostChoiceModal] = useState(false)

  // 유령 감지 배너 — 상단에 노출
  const [ghostBanner, setGhostBanner] = useState<{
    total: number
    markets: { market: string; count: number; summary: string }[]
  } | null>(null)
  useEffect(() => {
    // 오늘 날짜 스누즈 체크
    const today = new Date().toISOString().slice(0, 10)
    if (typeof window !== 'undefined' && window.localStorage.getItem('samba_ghost_banner_dismissed') === today) return
    let aborted = false
    ;(async () => {
      try {
        const res = await shipmentApi.ghostSummary(48)
        if (aborted) return
        if (res.total_count > 0) {
          setGhostBanner({
            total: res.total_count,
            markets: res.markets.map(m => ({ market: m.market, count: m.count, summary: m.summary })),
          })
        }
      } catch { /* 무시 */ }
    })()
    return () => { aborted = true }
  }, [])
  const marketLabel = (m: string) => m === '11st' ? '11번가' : m === 'lotteon' ? '롯데온' : m === 'smartstore' ? '스마트스토어' : m
  const dismissGhostBanner = () => {
    if (typeof window !== 'undefined') {
      const today = new Date().toISOString().slice(0, 10)
      window.localStorage.setItem('samba_ghost_banner_dismissed', today)
    }
    setGhostBanner(null)
  }

  // AI 작업 진행 모달
  const [aiJobModal, setAiJobModal] = useState(false)
  const [aiJobTitle, setAiJobTitle] = useState('')
  const [aiJobLogs, setAiJobLogs] = useState<string[]>([])
  const [aiJobDone, setAiJobDone] = useState(false)
  const aiJobAbortRef = useRef(false)
  const aiJobAbortControllerRef = useRef<AbortController | null>(null)
  const aiJobLogRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (aiJobLogRef.current) aiJobLogRef.current.scrollTop = aiJobLogRef.current.scrollHeight
  }, [aiJobLogs])

  // 배경제거 큐 — 현재 진행/대기 중인 잡 표시 (모달 열림 시 5초 폴링)
  type BgActiveJob = { job_id: string; status: string; total: number; current: number; created_at: string | null; started_at: string | null }
  const [bgActiveJobs, setBgActiveJobs] = useState<BgActiveJob[]>([])
  const [bgActiveLoaded, setBgActiveLoaded] = useState(false)
  const [bgWorkerAlive, setBgWorkerAlive] = useState(true)
  const [bgWorkerLastSeen, setBgWorkerLastSeen] = useState<string | null>(null)
  useEffect(() => {
    if (!aiJobModal) return
    let alive = true
    const tick = async () => {
      try {
        const res = await proxyApi.bgJobsActive()
        if (alive) {
          setBgActiveJobs(res.jobs || [])
          setBgActiveLoaded(true)
          setBgWorkerAlive(!!res.worker_alive)
          setBgWorkerLastSeen(res.worker_last_seen)
        }
      } catch { /* 일시 오류 무시 */ }
    }
    tick()
    const t = setInterval(tick, 5000)
    return () => { alive = false; clearInterval(t) }
  }, [aiJobModal])
  const cancelBgJob = async (jobId: string) => {
    if (!await showConfirm(`작업 ${jobId.slice(-8)}을 취소하시겠습니까?\n(진행 중이면 다음 상품 진입 전 중단됩니다)`)) return
    try {
      const res = await proxyApi.bgJobCancel(jobId)
      if (res.success) {
        showAlert('취소 완료 — 곧 워커가 다음 잡으로 넘어갑니다', 'success')
        try { const r = await proxyApi.bgJobsActive(); setBgActiveJobs(r.jobs || []) } catch { /* noop */ }
      } else {
        showAlert(`취소 실패: ${res.message || ''}`, 'error')
      }
    } catch (e) {
      showAlert(`취소 실패: ${e instanceof Error ? e.message : ''}`, 'error')
    }
  }

  // 가격재고갱신 모달
  const [refreshModal, setRefreshModal] = useState(false)
  const [refreshLoading, setRefreshLoading] = useState(false)
  const [refreshDetails, setRefreshDetails] = useState<RefreshDetail[]>([])
  const [refreshSummary, setRefreshSummary] = useState('')

  // 프리셋 이미지 목록 로드
  useEffect(() => {
    proxyApi.listPresets().then(res => {
      if (res.success) setAiPresetList(res.presets)
    }).catch(() => {})
  }, [])


  // 삭제 확인 모달
  const [deleteConfirm, setDeleteConfirm] = useState<{ ids: string[]; label: string } | null>(null);
  const [marketDeleteModal, setMarketDeleteModal] = useState<MarketDeleteModalState | null>(null)
  const formatDeleteLogProductLabel = useCallback((product?: SambaCollectedProduct | null) => {
    if (!product) return ''
    const sourceSite = (product.source_site || '').trim()
    const brand = (product.brand || '').trim()
    const productName = (product.name || product.id || '').trim().slice(0, 20)
    return [sourceSite, brand, productName].filter(Boolean).join(' / ')
  }, [])
  const abortAiJob = useCallback(() => {
    aiJobAbortRef.current = true
    aiJobAbortControllerRef.current?.abort()
    aiJobAbortControllerRef.current = null
  }, [])

  // 카테고리 매핑 (source_site::source_category → { market: category })
  const [catMappingMap, setCatMappingMap] = useState<Map<string, Record<string, string>>>(new Map())

  // AI 태그 미리보기 모달
  const [showTagPreview, setShowTagPreview] = useState(false)
  const [tagPreviews, setTagPreviews] = useState<{ group_id: string; group_name: string; product_count: number; product_ids?: string[]; rep_name: string; tags: string[]; seo_keywords: string[]; coupang_search_tags?: string[] }[]>([])
  const [tagPreviewCost, setTagPreviewCost] = useState<{ api_calls: number; input_tokens: number; output_tokens: number; cost_krw: number } | null>(null)
  const [tagPreviewLoading, setTagPreviewLoading] = useState(false)
  const [removedTags, setRemovedTags] = useState<string[]>([])

  // 삭제어 목록 (등록 상품명 취소선 표시용)
  const [deletionWords, setDeletionWords] = useState<string[]>([]);
  // 상품명 규칙 목록 (상품명 조합 적용용)
  const [nameRules, setNameRules] = useState<SambaNameRule[]>([]);

  // 서버사이드 페이지네이션 상품 로드 (counts도 함께 수신)
  const loadProducts = useCallback(async (page?: number) => {
    if (!queryReady) return
    const targetPage = page ?? currentPage
    setLoading(true)
    try {
      const skip = (targetPage - 1) * pageSize
      // status 필터에서 특수값 분리
      const knownStatus = ['has_orders', 'free_ship', 'same_day', 'free_same', 'market_registered', 'market_unregistered', 'sold_out']
      const statusParam = (knownStatus.includes(appliedStatusFilter) || appliedStatusFilter.startsWith('reg_') || appliedStatusFilter.startsWith('unreg_'))
        ? appliedStatusFilter : appliedStatusFilter || undefined
      const aiParam = (appliedAiFilter === 'has_orders') ? appliedAiFilter : appliedAiFilter || undefined
      const res = await collectorApi.scrollProducts({
        skip,
        limit: pageSize,
        search: appliedSearchQ.trim() || _idFilter || undefined,
        search_type: appliedSearchQ.trim() ? appliedSearchType : (_idFilter ? "id" : undefined),
        source_site: appliedSiteFilter || undefined,
        status: statusParam,
        sold_out_filter: appliedSoldOutFilter || undefined,
        ai_filter: aiParam,
        search_filter_id: appliedFilterByGroupId || undefined,
        sort_by: appliedSortBy,
      })
      setLoadError(false)
      setAllProducts(res.items)
      setServerTotal(res.total)
      setServerSites(res.sites)
      // scroll 응답에 counts 포함 — 별도 API 불필요
      if (res.counts) setKpiCounts(res.counts)
    } catch (e) {
      console.error("loadProducts error:", e)
      setLoadError(true)
    } finally {
      setLoading(false)
    }
  }, [queryReady, currentPage, pageSize, appliedSearchQ, appliedSearchType, _idFilter, appliedSiteFilter, appliedSoldOutFilter, appliedStatusFilter, appliedAiFilter, appliedFilterByGroupId, appliedSortBy])

  // 상품만 리로드 (삭제/수정 등 상품 변경 후 사용)
  const reloadProducts = useCallback(async () => {
    await loadProducts(currentPage)
  }, [loadProducts, currentPage])

  // 메타데이터 + 상품 로드 — 2-phase
  // Phase 1: scrollProducts만 먼저 → 상품 즉시 표시
  // Phase 2: 메타데이터 8개 백그라운드 → 정책/계정 정보 채움
  const load = useCallback(async () => {
    if (!queryReady) return
    const knownStatus2 = ['has_orders', 'free_ship', 'same_day', 'free_same', 'market_registered', 'market_unregistered', 'sold_out']
    const statusParam = (knownStatus2.includes(appliedStatusFilter) || appliedStatusFilter.startsWith('reg_') || appliedStatusFilter.startsWith('unreg_'))
      ? appliedStatusFilter : appliedStatusFilter || undefined
    const aiParam = (appliedAiFilter === 'has_orders') ? appliedAiFilter : appliedAiFilter || undefined

    // Phase 2를 Phase 1과 동시에 시작 (응답 처리는 scrollProducts 이후)
    // → 정책/계정 셀렉터가 조작 가능해지는 시점이 scrollProducts 응답시간만큼 앞당겨짐
    const metaPromise = collectorApi.initData()

    // Phase 1: 상품 목록만 먼저 (빠른 초기 렌더링)
    setLoading(true)
    try {
      const productsRes = await collectorApi.scrollProducts({
        skip: 0,
        limit: pageSize,
        search: appliedSearchQ.trim() || _idFilter || undefined,
        search_type: appliedSearchQ.trim() ? appliedSearchType : (_idFilter ? 'id' : undefined),
        source_site: appliedSiteFilter || undefined,
        status: statusParam,
        sold_out_filter: appliedSoldOutFilter || undefined,
        ai_filter: aiParam,
        search_filter_id: appliedFilterByGroupId || undefined,
        sort_by: appliedSortBy,
      }).catch(() => null)
      if (productsRes) {
        setLoadError(false)
        setAllProducts(productsRes.items)
        setServerTotal(productsRes.total)
        setServerSites(productsRes.sites)
        if (productsRes.counts) setKpiCounts(productsRes.counts)
      } else {
        setLoadError(true)
      }
    } catch (e) {
      console.error('load error:', e)
      setLoadError(true)
    } finally {
      setLoading(false)
    }

    // Phase 2: 메타데이터 백그라운드 로드 — 통합 엔드포인트 1회 호출 (기존 7개 개별 호출 대체)
    // load 진입 직후 시작된 metaPromise의 응답을 여기서 처리
    metaPromise.then(meta => {
      setPolicies(meta.policies ?? [])
      setAccounts(meta.accounts ?? [])
      setDetailTemplates(meta.detail_templates ?? [])
      setDeletionWords(meta.deletion_words ?? [])
      setNameRules(meta.name_rules ?? [])
      const nameMap: Record<string, string> = {}
      ;(meta.filters ?? []).forEach((f: SambaSearchFilter) => { nameMap[f.id] = f.name })
      setFilterNameMap(nameMap)
      setSearchFilters(meta.filters ?? [])
      const catMaps: { source_site: string; source_category: string; target_mappings: Record<string, string> }[] = meta.category_mappings ?? []
      if (Array.isArray(catMaps)) {
        const map = new Map<string, Record<string, string>>()
        catMaps.forEach(m => {
          map.set(`${m.source_site}::${m.source_category}`, m.target_mappings || {})
        })
        setCatMappingMap(map)
      }
    }).catch(e => console.error('metadata load error:', e))
  }, [queryReady, pageSize, appliedSearchQ, appliedSearchType, _idFilter, appliedSiteFilter, appliedSoldOutFilter, appliedStatusFilter, appliedAiFilter, appliedFilterByGroupId, appliedSortBy])

  useEffect(() => { load() }, [load])

  // 드롭다운 필터/정렬 변경 시 그룹 필터 자동 해제
  const groupClearInitRef = useRef(false)
  useEffect(() => {
    if (!queryReady) return
    if (!groupClearInitRef.current) return
    if (filterByGroupId) {
      setFilterByGroupId("")
      setFilterGroupName("")
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [queryReady, siteFilter, soldOutFilter, statusFilter, aiFilter, sortBy])

  // 필터/정렬 변경 시 1페이지로 리셋 + 선택 초기화 (디바운싱 300ms, 초기 로드 제외)
  const filterTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const filterInitRef = useRef(true)
  useEffect(() => {
    if (!queryReady) return
    // 첫 렌더는 스킵하고 ref를 flip — 이후 드롭다운 변경 시 디바운스 적용 활성화
    if (filterInitRef.current) { filterInitRef.current = false; return }
    setSelectAll(false)
    setSelectedIds(new Set())
    setCurrentPage(1)
    if (filterTimerRef.current) clearTimeout(filterTimerRef.current)
    filterTimerRef.current = setTimeout(() => {
      // 드롭다운 변경 시 applied 상태 동기화 — loadProducts는 appliedXxx 기준이므로
      // raw 상태만 바뀌면 OLD 필터로 호출되는 버그 방지 (예: AI이미지 미적용 필터 누락)
      setAppliedSiteFilter(siteFilter)
      setAppliedSoldOutFilter(soldOutFilter)
      setAppliedStatusFilter(statusFilter)
      setAppliedAiFilter(aiFilter)
      setAppliedSortBy(sortBy)
      // applied 상태 갱신 시 useEffect(() => load(), [load])가 자동 재조회
    }, 300)
    return () => { if (filterTimerRef.current) clearTimeout(filterTimerRef.current) }
  // searchType은 검색어가 있을 때만 재조회 트리거 (빈 검색어에서 드롭박스 변경 시 불필요한 로딩 방지)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [queryReady, searchQ, searchQ.trim() ? searchType : '', siteFilter, soldOutFilter, statusFilter, aiFilter, sortBy, filterByGroupId])

  // 페이지 변경 시 서버에서 해당 페이지 로드
  const totalPages = Math.max(1, Math.ceil(serverTotal / pageSize))
  const goToPage = useCallback((page: number) => {
    const p = Math.max(1, Math.min(page, totalPages))
    setCurrentPage(p)
    setSelectAll(false)
    setSelectedIds(new Set())
    loadProducts(p)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }, [totalPages, loadProducts])

  // pageSize 변경 시 1페이지로 리셋 (초기 로드 제외)
  // loadProducts를 deps에 넣으면 currentPage 변경 → loadProducts 재생성 → 이 effect가 발화하여
  // 2/3페이지로 이동해도 강제로 1페이지로 되돌리는 버그가 발생. pageSize만 감지해야 함.
  const pageSizeInitRef = useRef(true)
  useEffect(() => {
    if (!queryReady) return
    if (pageSizeInitRef.current) { pageSizeInitRef.current = false; return }
    setCurrentPage(1)
    loadProducts(1)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [queryReady, pageSize])

  // highlight 시 해당 상품만 표시, 아니면 전체
  const products = highlightProductId
    ? allProducts.filter(p => p.id === highlightProductId)
    : allProducts

  // KPI 카드용 — scroll 응답에 counts 포함, 별도 API 호출 불필요
  const [kpiCounts, setKpiCounts] = useState({ total: 0, registered: 0, policy_applied: 0, sold_out: 0 })
  const registeredCount = kpiCounts.registered

  const totalCount = serverTotal;

  const allSites = serverSites

  const handleSearch = () => {
    const nextGroupId = filterByGroupId ? "" : filterByGroupId
    // highlight + 그룹 필터 무조건 해제
    if (highlightProductId) setHighlightProductId("")
    if (filterByGroupId) {
      setFilterByGroupId("")
      setFilterGroupName("")
    }
    setAppliedSearchType(searchType)
    setAppliedSearchQ(searchQ)
    setAppliedSiteFilter(siteFilter)
    setAppliedSoldOutFilter(soldOutFilter)
    setAppliedStatusFilter(statusFilter)
    setAppliedAiFilter(aiFilter)
    setAppliedSortBy(sortBy)
    setAppliedFilterByGroupId(nextGroupId)
    setSelectAll(false)
    setSelectedIds(new Set())
    setCurrentPage(1)
  };

  const handleDelete = (id: string) => {
    const p = allProducts.find((x) => x.id === id);
    if (p?.lock_delete) {
      showAlert('삭제잠금이 설정된 상품입니다. 잠금을 해제한 후 삭제하세요.')
      return;
    }
    if (p && (p.registered_accounts?.length ?? 0) > 0) {
      openMarketDeleteModal([p], 'single', 'market')
      return;
    }
    setDeleteConfirm({ ids: [id], label: p ? `"${p.name.slice(0, 30)}"` : "이 상품" });
  };

  const fetchProductsByIds = useCallback(async (ids: string[]) => {
    const result: SambaCollectedProduct[] = []
    for (let i = 0; i < ids.length; i += 500) {
      const chunk = ids.slice(i, i + 500)
      const rows = await collectorApi.getProductsByIds(chunk)
      if (Array.isArray(rows)) result.push(...rows)
    }
    return result
  }, [])

  const handleBulkDelete = async () => {
    if (selectedIds.size === 0) return;
    // 전체선택 시 현재 페이지에 없는 상품도 서버에서 조회
    let selected = allProducts.filter(p => selectedIds.has(p.id))
    if (selected.length < selectedIds.size) {
      try {
        selected = await fetchProductsByIds([...selectedIds])
      } catch { /* 폴백: 현재 페이지만 */ }
    }
    const locked = selected.filter(p => p.lock_delete)
    const registered = selected.filter(p => !p.lock_delete && (p.registered_accounts?.length ?? 0) > 0)
    const deletableIds = selected
      .filter(p => !p.lock_delete && !(p.registered_accounts?.length))
      .map(p => p.id)
    if (deletableIds.length === 0) {
      const reasons = [
        locked.length > 0 ? `삭제잠금 ${fmt(locked.length)}개` : '',
        registered.length > 0 ? `마켓등록 ${fmt(registered.length)}개` : '',
      ].filter(Boolean).join(', ')
      showAlert(`삭제 가능한 상품이 없습니다 (${reasons})`)
      return;
    }
    const excludes = [
      locked.length > 0 ? `잠금 ${fmt(locked.length)}개` : '',
      registered.length > 0 ? `마켓등록 ${fmt(registered.length)}개` : '',
    ].filter(Boolean)
    const excludeMsg = excludes.length > 0 ? ` (${excludes.join(', ')} 제외)` : ''
    setDeleteConfirm({ ids: deletableIds, label: `${fmt(deletableIds.length)}개 상품${excludeMsg}` });
  };

  const handleLockToggle = async (productId: string, field: 'lock_delete' | 'lock_stock', value: boolean) => {
    // 낙관적 업데이트 (새로고침 없이 즉시 반영)
    setAllProducts(prev => prev.map(p =>
      p.id === productId ? { ...p, [field]: value } : p
    ))
    try {
      await collectorApi.updateProduct(productId, { [field]: value } as Partial<SambaCollectedProduct>)
    } catch (e) {
      console.error(`${field} 변경 실패:`, e)
      showAlert(`${field === 'lock_stock' ? '재고잠금' : '삭제잠금'} 변경에 실패했습니다.`, 'error')
      // 실패 시 원복
      setAllProducts(prev => prev.map(p =>
        p.id === productId ? { ...p, [field]: !value } : p
      ))
    }
  };

  const handleBlockCollect = async (productId: string) => {
    const p = allProducts.find((x) => x.id === productId);
    const productLabel = p?.name || '선택한 상품';
    const ok = await showConfirm(`"${productLabel}" 상품을 수집차단 + 삭제하시겠습니까?\n(동일 상품이 다시 수집되지 않습니다)`);
    if (!ok) throw new Error('cancelled');
    try {
      const res = await collectorApi.blockAndDelete([productId]);
      showAlert(`차단 ${fmt(res.blocked)}건, 삭제 ${fmt(res.deleted)}건 완료`, 'success');
      setSelectedIds(prev => {
        const next = new Set(prev);
        next.delete(productId);
        return next;
      });
      setSelectAll(false);
      reloadProducts();
    } catch (e) {
      showAlert(`수집차단 실패: ${e instanceof Error ? e.message : ''}`);
      throw e;
    }
  };

  const confirmDelete = async () => {
    if (!deleteConfirm) return
    const ids = deleteConfirm.ids
    setDeleteConfirm(null)
    setAiJobTitle(`삭제 (${fmt(ids.length)}건)`)
    setAiJobLogs([`${fmt(ids.length)}건 일괄 삭제 중...`])
    setAiJobDone(false)
    setAiJobModal(true)
    try {
      const res = await collectorApi.bulkDeleteProducts(ids)
      setAiJobLogs(prev => [...prev, `${fmt(res.deleted)}건 삭제 완료 ✓`])
    } catch {
      setAiJobLogs(prev => [...prev, `삭제 실패 ✗`])
    }
    setAiJobDone(true)
    setSelectedIds(new Set())
    setSelectAll(false)
    reloadProducts()
  }

  const handlePolicyChange = async (productId: string, policyId: string) => {
    // 낙관적 업데이트
    setAllProducts(prev => prev.map(p =>
      p.id === productId ? { ...p, applied_policy_id: policyId || undefined } as SambaCollectedProduct : p
    ))
    await collectorApi.updateProduct(productId, { applied_policy_id: policyId || undefined } as Partial<SambaCollectedProduct>).catch(() => {})
  };

  const handleEnrich = async (productId: string) => {
    const product = allProducts.find((p) => p.id === productId)
    const productName = (product?.name || productId).slice(0, 50)
    setActiveLog({ productId, message: `[업데이트 중] ${productName}` })
    try {
      const { API_BASE_URL: apiBase } = await import('@/config/api')
      const res = await fetchWithAuth(`${apiBase}/api/v1/samba/collector/enrich/${productId}`, { method: "POST" });
      const data = await res.json();
      if (res.ok && data.success) {
        const p = data.product
        const costVal = p?.cost || p?.sale_price
        const priceStr = costVal != null ? `₩${fmt(Number(costVal))}` : '-'
        const stockStr = p?.sale_status === 'preorder' ? '판매예정' : p?.sale_status === 'sold_out' || p?.is_sold_out ? '품절' : '재고있음'
        const now = fmtTime()
        const retransmitStr = data.retransmitted ? ` | 마켓 ${data.retransmit_accounts}계정 수정등록` : ''
        setActiveLog({ productId, message: `[${now}] ${productName} → ${priceStr} | ${stockStr}${retransmitStr}` })
        // 해당 상품만 갱신 (전체 새로고침 없음)
        if (p) {
          setAllProducts(prev => prev.map(item => item.id === productId ? { ...item, ...p } : item))
        }
      } else {
        setActiveLog({ productId, message: `[실패] ${productName} → ${data.message || data.detail || '상세 보강 실패'}` })
      }
    } catch {
      setActiveLog({ productId, message: `[오류] ${productName} → 서버 연결 실패` })
    }
  };

  const applyMarketDeleteSuccessState = useCallback((product: SambaCollectedProduct, successAccIds: string[]) => {
    const remaining = (product.registered_accounts ?? []).filter(id => !successAccIds.includes(id))
    const marketNos = product.market_product_nos || {}
    const removeKeys = new Set<string>(successAccIds)
    successAccIds.forEach(id => removeKeys.add(`${id}_origin`))
    const nextMarketNos = Object.fromEntries(
      Object.entries(marketNos).filter(([key]) => !removeKeys.has(key))
    )

    return {
      ...product,
      registered_accounts: remaining,
      market_product_nos: Object.keys(nextMarketNos).length ? nextMarketNos : null,
      status: remaining.length === 0 ? 'collected' : product.status,
    } as SambaCollectedProduct
  }, [])

  const openMarketDeleteModal = useCallback((targetProducts: SambaCollectedProduct[], mode: 'single' | 'bulk', deleteMode: 'market' | 'force' = 'market') => {
    const counts = new Map<string, number>()
    targetProducts.forEach(product => {
      ;(product.registered_accounts ?? []).forEach(accountId => {
        counts.set(accountId, (counts.get(accountId) ?? 0) + 1)
      })
    })

    const options = Array.from(counts.entries())
      .map(([accountId, productCount]) => {
        const account = accountsMap.get(accountId)
        // orphan 등록계정(계정 삭제·비활성으로 accountsMap에서 빠짐): drop 대신 옵션 유지.
        // 강제삭제로 DB 등록기록 정리 가능 (issue #362). marketType은 표시용이라 fallback 안전.
        if (!account) {
          return {
            accountId,
            label: `삭제된 계정 (${accountId.slice(0, 8)})`,
            marketType: '연결 끊김',
            productCount,
          }
        }
        const marketLabel = MARKETS.find(item => item.id === account.market_type)?.name || account.market_name || account.market_type
        return {
          accountId,
          label: `${marketLabel}${account.seller_id ? ` (${account.seller_id})` : ''}`,
          marketType: account.market_type,
          productCount,
        }
      })
      .filter((item): item is NonNullable<typeof item> => !!item)
      .sort((a, b) => a.label.localeCompare(b.label))

    if (!options.length) {
      showAlert('등록된 판매처가 없습니다.')
      return
    }

    const titlePrefix = deleteMode === 'force' ? '강제삭제' : '마켓삭제'
    setMarketDeleteModal({
      mode,
      deleteMode,
      title: mode === 'single'
        ? `${titlePrefix} - ${(targetProducts[0]?.name || targetProducts[0]?.id || '').slice(0, 20)}`
        : `${titlePrefix} (${fmt(targetProducts.length)}건)`,
      products: targetProducts,
      options,
      selectedAccountIds: options.map(option => option.accountId),
    })
  }, [accountsMap])

  const executeMarketDelete = useCallback(async (targetProducts: SambaCollectedProduct[], accountIds: string[], title: string, deleteMode: 'market' | 'force' = 'market') => {
    if (!accountIds.length) {
      showAlert('삭제할 판매처를 선택해주세요.')
      return
    }

    aiJobAbortRef.current = false
    aiJobAbortControllerRef.current = null
    setAiJobTitle(title)
    setAiJobLogs([])
    setAiJobDone(false)
    setAiJobModal(true)

    const logsRef: string[] = []
    const flushLogs = () => setAiJobLogs([...logsRef])
    const successMap = new Map<string, string[]>()
    const ts = fmtTime
    let totalOk = 0
    let totalFail = 0

    for (let i = 0; i < targetProducts.length; i++) {
      if (aiJobAbortRef.current) {
        logsRef.push(``, `중단됨 (${fmt(i)}/${fmt(targetProducts.length)})`)
        flushLogs()
        break
      }

      const product = targetProducts[i]
      const productName = formatDeleteLogProductLabel(product) || (product.name || product.id).slice(0, 20)
      const targetAccIds = accountIds.filter(id => (product.registered_accounts ?? []).includes(id))
      if (!targetAccIds.length) continue

      try {
        if (deleteMode === 'force') {
          // 강제삭제: 마켓 API 호출 없이 DB의 등록 정보만 제거
          await collectorApi.bulkResetRegistration([product.id], targetAccIds)
          const successAccIds = targetAccIds
          for (const accId of successAccIds) {
            const account = accountsMap.get(accId)
            const label = account
              ? (MARKETS.find(item => item.id === account.market_type)?.name || account.market_type)
              : accId.slice(0, 8)
            totalOk++
            logsRef.push(`[${ts()}] [${fmt(i + 1)}/${fmt(targetProducts.length)}] ${productName} -> ${label}: DB 정리`)
          }
          successMap.set(product.id, successAccIds)
        } else {
          // 마켓삭제: 실제 마켓 API 호출
          const controller = new AbortController()
          aiJobAbortControllerRef.current = controller
          const res = await shipmentApi.marketDelete([product.id], targetAccIds, undefined, undefined, false, controller.signal)
          const result = res?.results?.[0]
          if (result?.delete_results) {
            const entries = Object.entries(result.delete_results as Record<string, string>)
            const successAccIds: string[] = []
            for (const [accId, status] of entries) {
              const account = accountsMap.get(accId)
              const label = account
                ? (MARKETS.find(item => item.id === account.market_type)?.name || account.market_type)
                : accId.slice(0, 8)
              const isOk = status === 'success' || status.includes('성공')
              const isSoldout = status === 'soldout_fallback'
              if (isOk) {
                totalOk++
                successAccIds.push(accId)
              } else if (isSoldout) {
                totalOk++
                // 품절 처리 성공 — 등록 상태 유지 (successAccIds 미추가)
              } else {
                totalFail++
              }
              const mktNo = product.market_product_nos?.[accId]
              const mktNoStr = mktNo ? ` (${mktNo})` : ''
              const logMsg = isOk ? '성공' : isSoldout ? '품절 처리 완료 (주문 완료 후 재삭제)' : status
              logsRef.push(`[${ts()}] [${fmt(i + 1)}/${fmt(targetProducts.length)}] ${productName}${mktNoStr} -> ${label}: ${logMsg}`)
            }
            if (successAccIds.length) successMap.set(product.id, successAccIds)
          } else if (res?.processed === 0) {
            totalFail++
            logsRef.push(`[${ts()}] [${fmt(i + 1)}/${fmt(targetProducts.length)}] ${productName} -> 0건 처리됨 (비상정지 상태 확인 필요)`)
          } else {
            totalOk++
            logsRef.push(`[${ts()}] [${fmt(i + 1)}/${fmt(targetProducts.length)}] ${productName} -> 성공`)
          }
        }
      } catch (error) {
        if (error instanceof DOMException && error.name === 'AbortError') {
          logsRef.push(``, `ä»¥ë¬ë–’??(${fmt(i)}/${fmt(targetProducts.length)})`)
          flushLogs()
          break
        }
        totalFail++
        logsRef.push(`[${ts()}] [${fmt(i + 1)}/${fmt(targetProducts.length)}] ${productName} -> 오류`)
      }

      aiJobAbortControllerRef.current = null
      flushLogs()
      await new Promise(resolve => setTimeout(resolve, 50))
    }

    if (successMap.size > 0) {
      setAllProducts(prev => prev.map(product => {
        const successAccIds = successMap.get(product.id)
        return successAccIds ? applyMarketDeleteSuccessState(product, successAccIds) : product
      }))
    }

    logsRef.push(``, `성공 ${fmt(totalOk)} / 실패 ${fmt(totalFail)}`)
    flushLogs()
    setAiJobDone(true)
    reloadProducts()
  }, [accountsMap, applyMarketDeleteSuccessState, formatDeleteLogProductLabel])

  const handleMarketDelete = async (productId: string) => {
    const product = allProducts.find(x => x.id === productId)
    if (!product) return
    // 삭제잠금 가드 — 자동 경로(autotune/refresh)와 동일하게 수동 마켓삭제도 차단 (#301)
    if (product.lock_delete) {
      showAlert('삭제잠금이 설정된 상품입니다. 잠금을 해제한 후 마켓삭제하세요.')
      return
    }
    if (!(product.registered_accounts?.length ?? 0)) {
      showAlert('마켓에 등록된 계정이 없습니다.')
      return
    }
    openMarketDeleteModal([product], 'single')
    return
    const p = allProducts.find(x => x.id === productId)
    const regAccIds = p?.registered_accounts ?? []
    if (!regAccIds.length) {
      showAlert('마켓에 등록된 계정이 없습니다.')
      return
    }
    if (!await showConfirm('마켓에서 상품을 삭제(판매중지)하시겠습니까?')) return
    const productName = formatDeleteLogProductLabel(p) || (p?.name || productId).slice(0, 20)
    const ts = fmtTime
    setAiJobTitle(`마켓삭제 - ${productName}`)
    setAiJobLogs([])
    setAiJobDone(false)
    setAiJobModal(true)
    try {
      const res = await shipmentApi.marketDelete([productId], regAccIds)
      const result = res?.results?.[0]
      if (result?.delete_results) {
        const entries = Object.entries(result.delete_results as Record<string, string>)
        const logs = entries.map(([accId, status]) => {
          const acc = accountsMap.get(accId)
          const label = acc?.market_type || accId.slice(0, 8)
          const isOk = status === 'success' || status.includes('성공')
          return `[${ts()}] ${productName} → ${label}: ${isOk ? '✓' : '✗'}`
        })
        logs.push(`[${ts()}] 완료 — 성공 ${fmt(result.success_count)}/${fmt(entries.length)}`)
        setAiJobLogs(logs)
        const successAccIds = entries.filter(([, s]) => s === 'success' || (s as string).includes('성공')).map(([id]) => id)
        setAllProducts(prev => prev.map(pp => {
          if (pp.id !== productId) return pp
          const remaining = (pp.registered_accounts ?? []).filter(id => !successAccIds.includes(id))
          return { ...pp, registered_accounts: remaining, status: remaining.length === 0 ? 'collected' : pp.status } as SambaCollectedProduct
        }))
      } else if (!res?.results?.length || res?.processed === 0) {
        setAiJobLogs([`[${ts()}] ${productName} → 0건 처리됨 (비상정지 상태 확인 필요)`])
      } else {
        setAiJobLogs([`[${ts()}] ${productName} → ✓`])
      }
    } catch {
      setAiJobLogs([`[${ts()}] ${productName} → ✗ 오류`])
    }
    setAiJobDone(true)
  }

  const handleToggleMarket = async (productId: string, marketId: string) => {
    const product = allProducts.find((p) => p.id === productId);
    if (!product) return;
    const currentEnabled = (product.market_enabled || {}) as Record<string, boolean>;
    const isOn = currentEnabled[marketId] !== false;
    const newEnabled = { ...currentEnabled, [marketId]: !isOn };
    await collectorApi.updateProduct(productId, { market_enabled: newEnabled } as unknown as Partial<SambaCollectedProduct>).catch(() => {});
    // Optimistic update
    setAllProducts((prev) =>
      prev.map((p) =>
        p.id === productId ? { ...p, market_enabled: newEnabled } as unknown as SambaCollectedProduct : p
      )
    );
  };

  const handleSelectAll = async (checked: boolean) => {
    if (!checked) {
      setSelectAll(false);
      setSelectedIds(new Set());
      return;
    }
    // 단일 페이지면 현재 페이지 ID로 충분
    if (serverTotal <= products.length) {
      setSelectAll(true);
      setSelectedIds(new Set(products.map((p) => p.id)));
      return;
    }
    // 검색결과 전체 ID 조회 (1회 자동 재시도, 실패 시 무음 폴백 금지)
    setSelectAll(true);
    const fetchIds = () =>
      collectorApi.getProductIds({
        search: appliedSearchQ.trim() || undefined,
        search_type: appliedSearchQ.trim() ? appliedSearchType : undefined,
        source_site: appliedSiteFilter || undefined,
        status: appliedStatusFilter || undefined,
        sold_out_filter: appliedSoldOutFilter || undefined,
        ai_filter: appliedAiFilter || undefined,
        search_filter_id: appliedFilterByGroupId || undefined,
      })
    try {
      const res = await fetchIds()
      setSelectedIds(new Set(res.ids))
    } catch {
      await new Promise((r) => setTimeout(r, 600))
      try {
        const res = await fetchIds()
        setSelectedIds(new Set(res.ids))
      } catch {
        setSelectAll(false)
        setSelectedIds(new Set())
        showAlert('전체선택 실패: 잠시 후 다시 시도해주세요', 'error')
      }
    }
  };

  // 성능 최적화: 안정적인 콜백 참조로 ProductCard 불필요한 리렌더 방지
  const handleProductUpdate = useCallback((productId: string, data: Partial<SambaCollectedProduct>) => {
    setAllProducts(prev => prev.map(pp => pp.id === productId ? { ...pp, ...data } : pp))
    // 서버 저장이 필요한 필드만 화이트리스트 호출 (다른 로컬 상태 변경은 호출 생략)
    const persistKeys: (keyof SambaCollectedProduct)[] = ['coupang_search_tags', 'seo_keywords']
    const persistData: Partial<SambaCollectedProduct> = {}
    let need = false
    for (const k of persistKeys) {
      if (k in data) {
        (persistData as Record<string, unknown>)[k] = (data as Record<string, unknown>)[k]
        need = true
      }
    }
    if (need) {
      collectorApi.updateProduct(productId, persistData).catch(() => {})
    }
  }, [])

  const handleTagUpdate = useCallback(async (productId: string, tags: string[]) => {
    const userTags = tags.filter(t => !t.startsWith('__'))
    const clearSeo = userTags.length === 0
    setAllProducts(prev => prev.map(p =>
      p.id === productId ? { ...p, tags, ...(clearSeo ? { seo_keywords: [] } : {}) } : p
    ))
    const updateData: Partial<SambaCollectedProduct> = { tags }
    if (clearSeo) updateData.seo_keywords = []
    await collectorApi.updateProduct(productId, updateData).catch(() => {})
  }, [])

  const handleToggleExpand = useCallback((productId: string) => {
    setExpandedIds(prev => {
      const next = new Set(prev)
      if (next.has(productId)) next.delete(productId)
      else next.add(productId)
      return next
    })
  }, [])

  const handleCheckboxToggle = (id: string, checked: boolean) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (checked) next.add(id);
      else next.delete(id);
      return next;
    });
  };

  // 유령삭제 — 스마트스토어: Naver엔 있는데 DB 매핑 없는 고아 상품 정리 + DB→Naver 역고아 매핑 정리
  const runSmartstoreGhostSync = async () => {
    if (!await showConfirm('스마트스토어 동기화를 실행합니다.\n\n1단계: 스마트스토어 등록상품 전체를 수집합니다 (수 분 소요)\n2단계: 결과 확인 후 실제 처리 여부를 선택합니다\n  · 스마트스토어에만 있는 상품 → 스마트스토어에서 삭제\n  · 삼바에만 등록표시된 상품 → 삼바 등록표시 해제\n\n계속하시겠습니까?')) return

    setAiJobTitle('스마트스토어 동기화')
    setAiJobLogs(['고아 상품 조회 중... (Naver 상품 전체 페이징 수집 — 수 분 소요)'])
    setAiJobDone(false)
    setAiJobModal(true)

    try {
      const syncAccountId = appliedStatusFilter.startsWith('reg_') ? appliedStatusFilter.replace('reg_', '') : undefined
      let filteredIds: string[] = []
      try {
        const idRes = await collectorApi.getProductIds({
          search: appliedSearchQ.trim() || undefined,
          search_type: appliedSearchQ.trim() ? appliedSearchType : undefined,
          source_site: appliedSiteFilter || undefined,
          status: appliedStatusFilter || undefined,
          sold_out_filter: appliedSoldOutFilter || undefined,
          ai_filter: appliedAiFilter || undefined,
          search_filter_id: appliedFilterByGroupId || undefined,
        })
        filteredIds = idRes.ids ?? []
      } catch (idErr) {
        setAiJobLogs([`필터 ID 조회 실패: ${idErr instanceof Error ? idErr.message : String(idErr)}`])
        setAiJobDone(true)
        return
      }
      const res = await shipmentApi.cleanupSmartstoreOrphans(true, 50, syncAccountId, filteredIds)
      const dbCount = res.db_no_count ?? 0
      const staleCount = res.total_stale_db ?? 0
      const logs: string[] = [
        syncAccountId ? `계정 필터: ${syncAccountId}` : '전체 계정',
        `DB 등록 상품: ${fmt(dbCount)}개 (화면 필터)`,
        `Naver 등록 상품: ${fmt(res.total_naver)}개`,
        `Naver→DB 고아: ${fmt(res.total_orphans)}개 (Naver엔 있는데 DB 매핑 없음)`,
        `DB→Naver 역고아: ${fmt(staleCount)}개 (DB엔 판매중인데 Naver에 없음)`,
        '',
      ]
      for (const a of res.accounts) {
        if (a.error) {
          logs.push(`[${a.account_id}] ${a.error}`)
          continue
        }
        const failedPages = a.failed_pages ?? []
        const totalP = a.total_pages ?? 0
        const fpSuffix = failedPages.length > 0
          ? ` ⚠ 페이지 누락 ${fmt(failedPages.length)}/${fmt(totalP)}`
          : ''
        logs.push(`[${a.account_id}] Naver ${fmt(a.naver_count ?? 0)}개 / 고아 ${fmt(a.orphan_count ?? 0)}개 / 역고아 ${fmt(a.stale_db_count ?? 0)}개${fpSuffix}`)
        for (const o of (a.orphans ?? []).slice(0, 30)) {
          logs.push(`  [고아] ${o.origin_no}  ${o.name}`)
        }
        if ((a.orphans?.length ?? 0) > 30) {
          logs.push(`  ... 외 ${(a.orphans!.length - 30).toLocaleString()}개`)
        }
        for (const s of (a.stale_db ?? []).slice(0, 30)) {
          const sid = s.site_product_id ? ` 상품번호=${s.site_product_id}` : ''
          logs.push(`  [역고아]${sid}  originNo=${s.mapped_origin_no}  ${s.product_name}  (style=${s.style_code})`)
        }
        if ((a.stale_db?.length ?? 0) > 30) {
          logs.push(`  ... 외 ${(a.stale_db!.length - 30).toLocaleString()}개`)
        }
      }
      setAiJobLogs(logs)
      setAiJobDone(true)

      if (res.total_orphans === 0 && staleCount === 0) {
        logs.push('', '고아/역고아 상품이 없습니다.')
        setAiJobLogs([...logs])
        return
      }

      const totalToDelete = res.total_orphans
      const estSec = Math.ceil(totalToDelete * 0.4)
      const staleN = res.total_stale_db ?? 0
      const staleMsg = staleN > 0 ? `\n+ 역고아 ${fmt(staleN)}개 DB 매핑 자동 정리 (Naver 호출 없음)` : ''
      if (!await showConfirm(`고아 상품 ${fmt(totalToDelete)}개를 전부 삭제하시겠습니까?\n(예상 소요 ${fmt(estSec)}초 — 호출당 0.3초 throttle + 429 재시도)${staleMsg}`)) {
        logs.push('', '삭제 취소됨.')
        setAiJobLogs([...logs])
        return
      }

      logs.push('', `삭제 실행 중... (${fmt(totalToDelete)}개, 예상 ${fmt(estSec)}초)`)
      setAiJobLogs([...logs])
      setAiJobDone(false)

      const del = await shipmentApi.cleanupSmartstoreOrphans(false, totalToDelete, syncAccountId, filteredIds)
      logs.push(`고아 삭제 완료: ${fmt(del.total_deleted)}개`)
      if ((del.total_stale_cleared ?? 0) > 0) {
        logs.push(`역고아 DB 매핑 정리: ${fmt(del.total_stale_cleared!)}개`)
      }
      for (const a of del.accounts) {
        if (a.failed && a.failed.length > 0) {
          logs.push(`[${a.account_id}] 실패 ${fmt(a.failed.length)}건:`)
          for (const f of a.failed.slice(0, 10)) {
            logs.push(`  - ${f.origin_no}: ${f.error}`)
          }
        }
      }
      if (del.total_orphans > del.total_deleted) {
        logs.push('', `남은 고아 상품 ${fmt(del.total_orphans - del.total_deleted)}개 — 동기화 다시 실행하세요.`)
      }
      setAiJobLogs([...logs])
    } catch (e) {
      setAiJobLogs(prev => [...prev, '', `실패: ${e instanceof Error ? e.message : String(e)}`])
    }
    setAiJobDone(true)
  }

  // 유령삭제 — 11번가: registered만 있고 prdNo 없는 매핑 정리 (sellerPrdCd 역조회 → 판매중지 또는 DB 정리)
  const runElevenstGhostMissing = async () => {
    if (!await showConfirm('11번가 유령정리를 실행합니다.\n\nDB에 "11번가 등록됨"으로 표시되지만 상품번호(prdNo)가 비어있는 매핑을 찾아 정리합니다.\n\n1단계: 11번가 셀러상품코드로 역조회 (살아있음/판매종료/미존재 분류)\n2단계: 살아있으면 판매중지 + DB 정리, 죽었으면 DB만 정리\n\n계속하시겠습니까?')) return

    setAiJobTitle('11번가 유령정리 (prdNo 누락)')
    setAiJobLogs(['대상 조회 중...'])
    setAiJobDone(false)
    setAiJobModal(true)

    try {
      const syncAccountId = appliedStatusFilter.startsWith('reg_') ? appliedStatusFilter.replace('reg_', '') : undefined
      let filteredIds: string[] = []
      try {
        const idRes = await collectorApi.getProductIds({
          search: appliedSearchQ.trim() || undefined,
          search_type: appliedSearchQ.trim() ? appliedSearchType : undefined,
          source_site: appliedSiteFilter || undefined,
          status: appliedStatusFilter || undefined,
          sold_out_filter: appliedSoldOutFilter || undefined,
          ai_filter: appliedAiFilter || undefined,
          search_filter_id: appliedFilterByGroupId || undefined,
        })
        filteredIds = idRes.ids ?? []
      } catch (idErr) {
        setAiJobLogs([`필터 ID 조회 실패: ${idErr instanceof Error ? idErr.message : String(idErr)}`])
        setAiJobDone(true)
        return
      }

      const res = await shipmentApi.cleanupElevenstMissingPrdno(true, 500, syncAccountId, filteredIds)
      const logs: string[] = [
        syncAccountId ? `계정 필터: ${syncAccountId}` : '전체 11번가 계정',
        `점검 대상: ${fmt(res.total_checked)}개`,
        `  살아있음(판매중): ${fmt(res.total_alive)}개 → 판매중지 후 DB 정리 예정`,
        `  판매종료: ${fmt(res.total_dead)}개 → DB만 정리 예정`,
        `  11번가에도 없음: ${fmt(res.total_missing)}개 → DB만 정리 예정`,
        `  실패: ${fmt(res.total_failed)}개`,
        '',
      ]
      for (const a of res.accounts) {
        if (a.error) {
          logs.push(`[${a.account_id}] ${a.error}`)
          continue
        }
        logs.push(`[${a.label || a.account_id}] 점검 ${fmt(a.checked ?? 0)} / 살아있음 ${fmt(a.alive_count ?? 0)} / 종료 ${fmt(a.dead_count ?? 0)} / 미존재 ${fmt(a.missing_count ?? 0)} / 실패 ${fmt(a.failed_count ?? 0)}`)
        for (const it of (a.alive ?? []).slice(0, 30)) {
          logs.push(`  [살아있음] prdNo=${it.prd_no}  ${it.name}  (${it.sel_stat_nm || it.sel_stat_cd})`)
        }
        if ((a.alive?.length ?? 0) > 30) logs.push(`  ... 외 ${((a.alive?.length ?? 0) - 30).toLocaleString()}개`)
        for (const it of (a.dead ?? []).slice(0, 20)) {
          logs.push(`  [종료] prdNo=${it.prd_no}  ${it.name}  (${it.sel_stat_nm || it.sel_stat_cd})`)
        }
        for (const it of (a.missing ?? []).slice(0, 20)) {
          logs.push(`  [미존재] sellerCode=${it.seller_code}  ${it.name}`)
        }
        for (const f of (a.failed ?? []).slice(0, 10)) {
          logs.push(`  [실패] ${f.product_id}: ${f.error}`)
        }
      }
      setAiJobLogs(logs)
      setAiJobDone(true)

      const totalToProcess = res.total_alive + res.total_dead + res.total_missing
      if (totalToProcess === 0) {
        logs.push('', '정리할 유령 매핑이 없습니다.')
        setAiJobLogs([...logs])
        return
      }
      const estSec = Math.ceil(res.total_alive * 0.8 + (res.total_dead + res.total_missing) * 0.1)
      if (!await showConfirm(`총 ${fmt(totalToProcess)}건을 정리하시겠습니까?\n- 살아있음 ${fmt(res.total_alive)}건: 11번가 판매중지 + DB 정리\n- 종료 ${fmt(res.total_dead)}건: DB만 정리\n- 미존재 ${fmt(res.total_missing)}건: DB만 정리\n\n예상 소요 ${fmt(estSec)}초 (호출당 0.4초 throttle)`)) {
        logs.push('', '정리 취소됨.')
        setAiJobLogs([...logs])
        return
      }

      logs.push('', `정리 실행 중... (${fmt(totalToProcess)}건, 예상 ${fmt(estSec)}초)`)
      setAiJobLogs([...logs])
      setAiJobDone(false)

      const del = await shipmentApi.cleanupElevenstMissingPrdno(false, 500, syncAccountId, filteredIds)
      logs.push(`복구(판매중지+DB정리): ${fmt(del.total_recovered)}건`)
      logs.push(`DB 매핑 정리: ${fmt(del.total_db_cleared)}건`)
      if (del.total_failed > 0) logs.push(`실패: ${fmt(del.total_failed)}건`)
      for (const a of del.accounts) {
        if (a.failed && a.failed.length > 0) {
          logs.push(`[${a.label || a.account_id}] 실패 ${fmt(a.failed.length)}건:`)
          for (const f of a.failed.slice(0, 10)) {
            logs.push(`  - ${f.product_id}: ${f.error}`)
          }
        }
      }
      setAiJobLogs([...logs])
    } catch (e) {
      setAiJobLogs(prev => [...prev, '', `실패: ${e instanceof Error ? e.message : String(e)}`])
    }
    setAiJobDone(true)
  }

  // 유령삭제 — 양방향 공통 러너: 스마트스토어 패턴(쿠팡/11번가v2/롯데ON)
  const runBidirectionalGhostSync = async (
    marketLabel: string,
    apiFn: (dryRun: boolean, maxDelete: number, accountId?: string, productIds?: string[]) => Promise<{
      ok: boolean
      total_market: number
      total_orphans: number
      total_stale_db: number
      total_deleted: number
      total_stale_cleared: number
      accounts: Array<{
        account_id: string
        label?: string
        error?: string
        market_count?: number
        orphan_count?: number
        orphans?: Array<Record<string, string>>
        stale_db_count?: number
        stale_db?: Array<Record<string, string>>
        deleted?: string[]
        failed?: Array<Record<string, string>>
        recovered_via_seller_code?: number
      }>
    }>,
    orphanLabel: { idKey: string; name: string },
  ) => {
    if (!await showConfirm(`${marketLabel} 동기화를 실행합니다.\n\n1단계: ${marketLabel} 등록상품 전체를 수집합니다 (수 분 소요)\n2단계: 결과 확인 후 실제 처리 여부를 선택합니다\n  · ${marketLabel}에만 있는 상품 → ${marketLabel}에서 삭제\n  · 삼바에만 등록표시된 상품 → 삼바 등록표시 해제\n\n계속하시겠습니까?`)) return

    setAiJobTitle(`${marketLabel} 동기화`)
    setAiJobLogs([`목록 수집 중... (${marketLabel} 전체 페이징 — 수 분 소요)`])
    setAiJobDone(false)
    setAiJobModal(true)

    try {
      const syncAccountId = appliedStatusFilter.startsWith('reg_') ? appliedStatusFilter.replace('reg_', '') : undefined
      let filteredIds: string[] = []
      try {
        const idRes = await collectorApi.getProductIds({
          search: appliedSearchQ.trim() || undefined,
          search_type: appliedSearchQ.trim() ? appliedSearchType : undefined,
          source_site: appliedSiteFilter || undefined,
          status: appliedStatusFilter || undefined,
          sold_out_filter: appliedSoldOutFilter || undefined,
          ai_filter: appliedAiFilter || undefined,
          search_filter_id: appliedFilterByGroupId || undefined,
        })
        filteredIds = idRes.ids ?? []
      } catch (idErr) {
        setAiJobLogs([`필터 ID 조회 실패: ${idErr instanceof Error ? idErr.message : String(idErr)}`])
        setAiJobDone(true)
        return
      }

      const res = await apiFn(true, 50, syncAccountId, filteredIds)
      const logs: string[] = [
        syncAccountId ? `계정 필터: ${syncAccountId}` : `전체 ${marketLabel} 계정`,
        `${marketLabel} 등록상품: ${fmt(res.total_market)}개`,
        `${marketLabel}에만 있는 상품: ${fmt(res.total_orphans)}개 → ${marketLabel}에서 삭제 예정`,
        `삼바에만 등록표시된 상품: ${fmt(res.total_stale_db)}개 → 등록표시 해제 예정`,
        '',
      ]
      for (const a of res.accounts) {
        if (a.error) {
          logs.push(`[${a.label || a.account_id}] ${a.error}`)
          continue
        }
        const rec = a.recovered_via_seller_code ? ` / 셀러코드보강 ${fmt(a.recovered_via_seller_code)}` : ''
        logs.push(`[${a.label || a.account_id}] ${marketLabel} ${fmt(a.market_count ?? 0)}개 / 마켓에만 ${fmt(a.orphan_count ?? 0)}개 / 삼바에만 ${fmt(a.stale_db_count ?? 0)}개${rec}`)
        for (const o of (a.orphans ?? []).slice(0, 30)) {
          logs.push(`  [마켓에만] ${orphanLabel.idKey}=${o[orphanLabel.idKey] || ''}  ${o.name || ''}`)
        }
        if ((a.orphans?.length ?? 0) > 30) logs.push(`  ... 외 ${fmt((a.orphans!.length) - 30)}개`)
        for (const s of (a.stale_db ?? []).slice(0, 20)) {
          logs.push(`  [삼바에만] db=${s.db_id || ''}  ${s.product_name || ''}`)
        }
        if ((a.stale_db?.length ?? 0) > 20) logs.push(`  ... 외 ${fmt((a.stale_db!.length) - 20)}개`)
      }
      setAiJobLogs(logs)
      setAiJobDone(true)

      const totalToProcess = res.total_orphans + res.total_stale_db
      if (totalToProcess === 0) {
        logs.push('', '차이 없음 — 삼바 DB와 마켓이 이미 일치합니다.')
        setAiJobLogs([...logs])
        return
      }

      const estSec = Math.ceil(res.total_orphans * 0.5)
      if (!await showConfirm(`총 ${fmt(totalToProcess)}건을 처리하시겠습니까?\n· ${marketLabel}에만 있는 상품 ${fmt(res.total_orphans)}건 → ${marketLabel}에서 삭제 (예상 ${fmt(estSec)}초)\n· 삼바에만 등록표시된 상품 ${fmt(res.total_stale_db)}건 → 삼바 등록표시 해제`)) {
        logs.push('', '처리 취소됨.')
        setAiJobLogs([...logs])
        return
      }

      logs.push('', `처리 실행 중... (마켓 삭제 ${fmt(res.total_orphans)}건, 예상 ${fmt(estSec)}초)`)
      setAiJobLogs([...logs])
      setAiJobDone(false)

      const del = await apiFn(false, res.total_orphans, syncAccountId, filteredIds)
      logs.push(`${marketLabel} 삭제 완료: ${fmt(del.total_deleted)}건`)
      logs.push(`삼바 등록표시 해제: ${fmt(del.total_stale_cleared)}건`)
      for (const a of del.accounts) {
        if (a.failed && a.failed.length > 0) {
          logs.push(`[${a.label || a.account_id}] 실패 ${fmt(a.failed.length)}건:`)
          for (const f of a.failed.slice(0, 10)) {
            const idVal = f[orphanLabel.idKey] || f.spid || f.prd_no || f.spd_no || ''
            logs.push(`  - ${idVal}: ${f.error}`)
          }
        }
      }
      setAiJobLogs([...logs])
    } catch (e) {
      setAiJobLogs(prev => [...prev, '', `실패: ${e instanceof Error ? e.message : String(e)}`])
    }
    setAiJobDone(true)
  }

  // 쿠팡 유령삭제 — 단건 스트리밍 러너 (stale DB 먼저, orphan 나중, 1건씩 로그)
  const runCoupangGhostSync = async () => {
    if (!await showConfirm('쿠팡 동기화를 실행합니다.\n\n1단계: 쿠팡 등록상품 전체를 수집합니다 (수 분 소요)\n2단계: 결과 확인 후 실제 처리 여부를 선택합니다\n  · 삼바에만 등록표시된 상품 → DB 등록표시 해제 (먼저, 빠름)\n  · 쿠팡에만 있는 상품 → 쿠팡에서 삭제 (1건씩 진행 로그)\n\n계속하시겠습니까?')) return

    setAiJobTitle('쿠팡 동기화')
    setAiJobLogs(['목록 수집 중... (쿠팡 전체 페이징 — 수 분 소요)'])
    setAiJobDone(false)
    setAiJobModal(true)

    try {
      const syncAccountId = appliedStatusFilter.startsWith('reg_') ? appliedStatusFilter.replace('reg_', '') : undefined
      let filteredIds: string[] = []
      try {
        const idRes = await collectorApi.getProductIds({
          search: appliedSearchQ.trim() || undefined,
          search_type: appliedSearchQ.trim() ? appliedSearchType : undefined,
          source_site: appliedSiteFilter || undefined,
          status: appliedStatusFilter || undefined,
          sold_out_filter: appliedSoldOutFilter || undefined,
          ai_filter: appliedAiFilter || undefined,
          search_filter_id: appliedFilterByGroupId || undefined,
        })
        filteredIds = idRes.ids ?? []
      } catch (idErr) {
        setAiJobLogs([`필터 ID 조회 실패: ${idErr instanceof Error ? idErr.message : String(idErr)}`])
        setAiJobDone(true)
        return
      }

      const res = await shipmentApi.cleanupCoupangOrphans(true, 100000, syncAccountId, filteredIds, true)
      const logs: string[] = [
        syncAccountId ? `계정 필터: ${syncAccountId}` : `전체 쿠팡 계정`,
        `쿠팡 등록상품: ${fmt(res.total_market)}개`,
        `쿠팡에만 있는 상품(orphan): ${fmt(res.total_orphans)}개`,
        `삼바에만 등록표시된 상품(stale): ${fmt(res.total_stale_db)}개`,
        '',
      ]

      type StaleItem = { account_id: string; db_id: string; product_name: string; style_code: string }
      type OrphanItem = { account_id: string; spid: string; name: string; status_name: string }
      const staleList: StaleItem[] = []
      const orphanList: OrphanItem[] = []
      for (const a of res.accounts) {
        if (a.error) { logs.push(`[${a.label || a.account_id}] ${a.error}`); continue }
        for (const s of (a.stale_db ?? [])) {
          if (s.db_id) staleList.push({ account_id: a.account_id, db_id: s.db_id, product_name: s.product_name || '', style_code: s.style_code || '' })
        }
        for (const o of (a.orphans ?? [])) {
          if (o.spid) orphanList.push({ account_id: a.account_id, spid: o.spid, name: o.name || '', status_name: o.status_name || '' })
        }
      }
      setAiJobLogs([...logs])
      setAiJobDone(true)

      const totalToProcess = staleList.length + orphanList.length
      if (totalToProcess === 0) {
        logs.push('차이 없음 — 삼바 DB와 쿠팡이 이미 일치합니다.')
        setAiJobLogs([...logs])
        return
      }

      if (!await showConfirm(`총 ${fmt(totalToProcess)}건을 처리하시겠습니까?\n· 삼바 DB 등록표시 해제 ${fmt(staleList.length)}건 (먼저, 1건씩)\n· 쿠팡 삭제 ${fmt(orphanList.length)}건 (나중, 1건씩 ~0.5초/건)`)) {
        logs.push('처리 취소됨.')
        setAiJobLogs([...logs])
        return
      }

      setAiJobDone(false)

      // 1단계: stale DB 1건씩 정리 (빠름)
      logs.push(`▶ DB 등록표시 해제 시작 (${fmt(staleList.length)}건)`)
      setAiJobLogs([...logs])
      let staleOk = 0
      let staleFail = 0
      for (let i = 0; i < staleList.length; i++) {
        const s = staleList[i]
        const idx = `${fmt(i + 1)}/${fmt(staleList.length)}`
        const sLabel = `${s.style_code ? s.style_code + ' ' : ''}${s.product_name.slice(0, 40)}`
        try {
          const r = await shipmentApi.clearCoupangStaleMapping(s.account_id, s.db_id)
          if (r.ok) {
            staleOk++
            logs.push(`[삼바해제 ${idx}] db=${s.db_id} ${sLabel} → ${r.cleared ? '완료' : '변경없음'}`)
          } else {
            staleFail++
            logs.push(`[삼바해제 ${idx}] db=${s.db_id} ${sLabel} 실패: ${r.error || '알수없음'}`)
          }
        } catch (e) {
          staleFail++
          logs.push(`[삼바해제 ${idx}] db=${s.db_id} ${sLabel} 실패: ${e instanceof Error ? e.message : String(e)}`)
        }
        if ((i + 1) % 5 === 0 || i === staleList.length - 1) setAiJobLogs([...logs])
      }
      logs.push(`▶ DB 등록표시 해제 완료: 성공 ${fmt(staleOk)}건 / 실패 ${fmt(staleFail)}건`)
      logs.push('')
      setAiJobLogs([...logs])

      // 2단계: orphan 쿠팡 삭제 1건씩
      logs.push(`▶ 쿠팡 삭제 시작 (${fmt(orphanList.length)}건)`)
      setAiJobLogs([...logs])
      let orphanOk = 0
      let orphanFail = 0
      for (let i = 0; i < orphanList.length; i++) {
        const o = orphanList[i]
        const idx = `${fmt(i + 1)}/${fmt(orphanList.length)}`
        const oLabel = `${o.status_name ? '[' + o.status_name + '] ' : ''}${o.name.slice(0, 50) || '(상품명없음)'}`
        try {
          const r = await shipmentApi.deleteCoupangOrphan(o.account_id, o.spid)
          if (r.ok) {
            orphanOk++
            const tail = r.message ? ` (${r.message})` : ''
            logs.push(`[쿠팡삭제 ${idx}] spid=${o.spid} ${oLabel} → 완료${tail}`)
          } else {
            orphanFail++
            logs.push(`[쿠팡삭제 ${idx}] spid=${o.spid} ${oLabel} 실패: ${r.error || '알수없음'}`)
          }
        } catch (e) {
          orphanFail++
          logs.push(`[쿠팡삭제 ${idx}] spid=${o.spid} ${oLabel} 실패: ${e instanceof Error ? e.message : String(e)}`)
        }
        if ((i + 1) % 3 === 0 || i === orphanList.length - 1) setAiJobLogs([...logs])
      }
      logs.push(`▶ 쿠팡 삭제 완료: 성공 ${fmt(orphanOk)}건 / 실패 ${fmt(orphanFail)}건`)
      setAiJobLogs([...logs])
    } catch (e) {
      setAiJobLogs(prev => [...prev, '', `실패: ${e instanceof Error ? e.message : String(e)}`])
    }
    setAiJobDone(true)
  }
  const runElevenstGhostSyncV2 = () => runBidirectionalGhostSync('11번가', shipmentApi.cleanupElevenstOrphansV2, { idKey: 'prd_no', name: 'name' })
  const runLotteonGhostSync = () => runBidirectionalGhostSync('롯데ON', shipmentApi.cleanupLotteonOrphans, { idKey: 'spd_no', name: 'name' })

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "0" }}>
      {/* 무신사 자동로그인계정 미설정/만료 경고 모달 */}
      {musinsaAuthMissing && !musinsaAuthDismissed && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.75)', backdropFilter: 'blur(4px)', zIndex: 9999, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <div style={{ background: '#1A1A1A', border: '2px solid #FF4444', borderRadius: '16px', padding: '2rem', maxWidth: '480px', width: '90%', boxShadow: '0 8px 32px rgba(255,68,68,0.3)', position: 'relative' }}>
            <button
              aria-label='알람 닫기'
              title='닫기'
              onClick={() => setMusinsaAuthDismissed(true)}
              style={{ position: 'absolute', top: '0.75rem', right: '0.75rem', width: '28px', height: '28px', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'transparent', border: 'none', borderRadius: '6px', color: '#AAA', fontSize: '1.25rem', fontWeight: 700, cursor: 'pointer', lineHeight: 1 }}
            >
              &#10005;
            </button>
            <div style={{ textAlign: 'center', marginBottom: '1.5rem' }}>
              <div style={{ fontSize: '3rem', marginBottom: '0.75rem' }}>&#9888;</div>
              <h3 style={{ fontSize: '1.25rem', fontWeight: 700, color: '#FF6B6B', marginBottom: '0.5rem' }}>무신사 원가 갱신 중단</h3>
              <p style={{ fontSize: '0.875rem', color: '#AAA', lineHeight: 1.5 }}>
                {musinsaAuthMissing.reason === 'cookie_expired'
                  ? <>자동로그인계정 <b style={{ color: '#FFD' }}>{musinsaAuthMissing.account_label}</b>의 쿠키가 만료됨. 무신사 재로그인 필요.</>
                  : musinsaAuthMissing.reason === 'no_cookie'
                  ? <>자동로그인계정 <b style={{ color: '#FFD' }}>{musinsaAuthMissing.account_label}</b>에 쿠키 없음. 무신사 로그인 필요.</>
                  : <>무신사 자동로그인계정 미설정. <b style={{ color: '#FFD' }}>설정 → 소싱처계정</b>에서 자동로그인 계정을 지정하세요.</>}
                <br/>
                <span style={{ color: '#FF8888' }}>cost 계산이 일관되지 않아 자동 갱신을 차단했습니다.</span>
              </p>
            </div>
            <div style={{ display: 'flex', gap: '0.5rem' }}>
              <button
                onClick={() => setMusinsaAuthDismissed(true)}
                style={{ flex: 1, padding: '0.75rem', background: 'transparent', border: '1px solid #444', borderRadius: '8px', color: '#AAA', fontSize: '0.9375rem', fontWeight: 600, cursor: 'pointer' }}
              >
                나중에
              </button>
              <button
                onClick={() => { window.location.href = '/samba/settings#sourcing-accounts-MUSINSA' }}
                style={{ flex: 2, padding: '0.75rem', background: '#FF4444', border: 'none', borderRadius: '8px', color: '#fff', fontSize: '0.9375rem', fontWeight: 700, cursor: 'pointer' }}
              >
                지금 설정하기
              </button>
            </div>
          </div>
        </div>
      )}

      {ghostBanner && ghostBanner.total > 0 && (
        <div style={{
          padding: '10px 16px', margin: '8px 12px 0',
          borderRadius: '8px',
          background: 'rgba(255,107,107,0.12)', border: '1px solid #FF6B6B',
          color: '#FF6B6B', fontSize: '0.82rem', fontWeight: 600,
          display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '12px',
          flexWrap: 'wrap',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
            <span>⚠ 유령 매핑 감지 (최근 48시간)</span>
            <span style={{ color: '#FFD0D0', fontWeight: 400 }}>
              {ghostBanner.markets.map(m => `${marketLabel(m.market)} ${fmt(m.count)}건`).join(' · ')}
            </span>
          </div>
          <div style={{ display: 'flex', gap: '8px' }}>
            <button
              onClick={() => setGhostChoiceModal(true)}
              style={{
                fontSize: '0.78rem', padding: '4px 12px', fontWeight: 600,
                border: '1px solid #FF6B6B', borderRadius: '6px',
                color: '#FFF', background: '#FF6B6B', cursor: 'pointer',
              }}
            >정리하기</button>
            <button
              onClick={dismissGhostBanner}
              style={{
                fontSize: '0.78rem', padding: '4px 10px',
                border: '1px solid #FF6B6B', borderRadius: '6px',
                color: '#FF6B6B', background: 'transparent', cursor: 'pointer',
              }}
            >오늘 그만보기</button>
          </div>
        </div>
      )}
      {/* AI 작업 진행 모달 */}
      {marketDeleteModal && (
        <div style={{
          position: 'fixed', inset: 0, zIndex: 99998,
          background: 'rgba(0,0,0,0.7)', display: 'flex', alignItems: 'center', justifyContent: 'center',
        }} onClick={() => setMarketDeleteModal(null)}>
          <div style={{
            background: '#1A1A1A', border: '1px solid #2D2D2D', borderRadius: '12px',
            width: 'min(520px, 92vw)', maxHeight: '75vh', display: 'flex', flexDirection: 'column',
          }} onClick={e => e.stopPropagation()}>
            <div style={{
              padding: '14px 20px', borderBottom: '1px solid #2D2D2D',
              display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            }}>
              <span style={{ fontWeight: 700, fontSize: '0.9rem', color: '#E5E5E5' }}>{marketDeleteModal.title}</span>
              <button onClick={() => setMarketDeleteModal(null)} style={{
                background: 'none', border: 'none', color: '#888', fontSize: '0.77rem', cursor: 'pointer',
              }}>닫기</button>
            </div>
            <div style={{ padding: '16px 20px 10px', color: '#A8B0C0', fontSize: '0.8rem', lineHeight: 1.6 }}>
              삭제할 판매처를 선택하세요. 선택한 판매처에 등록된 상품만 삭제됩니다.
            </div>
            <div style={{ padding: '0 20px 16px', display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
              <button onClick={() => setMarketDeleteModal(prev => prev ? { ...prev, selectedAccountIds: prev.options.map(option => option.accountId) } : prev)} style={{
                padding: '5px 10px', borderRadius: '6px', border: '1px solid #3D3D3D', background: '#222', color: '#D0D0D0', cursor: 'pointer', fontSize: '0.75rem',
              }}>전체선택</button>
              <button onClick={() => setMarketDeleteModal(prev => prev ? { ...prev, selectedAccountIds: [] } : prev)} style={{
                padding: '5px 10px', borderRadius: '6px', border: '1px solid #3D3D3D', background: '#222', color: '#D0D0D0', cursor: 'pointer', fontSize: '0.75rem',
              }}>선택해제</button>
            </div>
            <div style={{ padding: '0 20px 20px', overflow: 'auto', display: 'flex', flexDirection: 'column', gap: '10px' }}>
              {marketDeleteModal.options.map(option => {
                const checked = marketDeleteModal.selectedAccountIds.includes(option.accountId)
                return (
                  <label key={option.accountId} style={{
                    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                    gap: '12px', padding: '12px 14px', borderRadius: '8px',
                    border: checked ? '1px solid rgba(255,140,0,0.55)' : '1px solid #2D2D2D',
                    background: checked ? 'rgba(255,140,0,0.08)' : '#161616',
                    cursor: 'pointer',
                  }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => setMarketDeleteModal(prev => {
                          if (!prev) return prev
                          const selected = prev.selectedAccountIds.includes(option.accountId)
                            ? prev.selectedAccountIds.filter(id => id !== option.accountId)
                            : [...prev.selectedAccountIds, option.accountId]
                          return { ...prev, selectedAccountIds: selected }
                        })}
                      />
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                        <span style={{ color: '#E5E5E5', fontSize: '0.82rem', fontWeight: 600 }}>{option.label}</span>
                        <span style={{ color: '#8A95B0', fontSize: '0.74rem' }}>{option.marketType}</span>
                      </div>
                    </div>
                    <span style={{ color: '#FFB84D', fontSize: '0.75rem', whiteSpace: 'nowrap' }}>
                      {fmt(option.productCount)}개 상품
                    </span>
                  </label>
                )
              })}
            </div>
            <div style={{
              padding: '14px 20px', borderTop: '1px solid #2D2D2D',
              display: 'flex', justifyContent: 'flex-end', gap: '8px',
            }}>
              <button onClick={() => setMarketDeleteModal(null)} style={{
                padding: '7px 16px', borderRadius: '6px', border: '1px solid #3D3D3D',
                background: '#222', color: '#AAA', cursor: 'pointer',
              }}>취소</button>
              <button onClick={async () => {
                if (!marketDeleteModal.selectedAccountIds.length) {
                  showAlert('삭제할 판매처를 선택해주세요.')
                  return
                }
                const modal = marketDeleteModal
                setMarketDeleteModal(null)
                await executeMarketDelete(modal.products, modal.selectedAccountIds, modal.title, modal.deleteMode)
              }} style={{
                padding: '7px 16px', borderRadius: '6px', border: 'none',
                background: '#FF6B6B', color: '#FFF', cursor: 'pointer', fontWeight: 700,
              }}>삭제 실행</button>
            </div>
          </div>
        </div>
      )}

      {ghostChoiceModal && (
        <div
          style={{
            position: 'fixed', inset: 0, zIndex: 99999,
            background: 'rgba(0,0,0,0.7)', display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}
          onClick={() => setGhostChoiceModal(false)}
        >
          <div
            style={{
              background: '#1A1A1A', border: '1px solid #2D2D2D', borderRadius: '12px',
              width: '420px', padding: '20px',
            }}
            onClick={e => e.stopPropagation()}
          >
            <div style={{ fontWeight: 700, fontSize: '0.9rem', color: '#E5E5E5', marginBottom: '8px' }}>유령삭제 — 마켓 선택</div>
            <div style={{ fontSize: '0.78rem', color: '#888', marginBottom: '14px', lineHeight: 1.5 }}>
              삼바 DB와 마켓 등록상품 목록을 100% 일치시킵니다.<br />
              · 마켓에만 있는 상품 → 마켓에서 삭제<br />
              · 삼바에만 등록 표시된 상품 → 삼바 등록표시 해제<br />
              점검할 마켓을 선택하세요. (화면 필터가 적용된 상품 범위에서만 점검)
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
              <button
                onClick={() => { setGhostChoiceModal(false); runSmartstoreGhostSync() }}
                style={{
                  padding: '10px 14px', fontSize: '0.82rem', fontWeight: 600,
                  border: '1px solid #3D3D3D', borderRadius: '8px', color: '#E5E5E5',
                  background: 'rgba(80,140,255,0.15)', cursor: 'pointer', textAlign: 'left',
                }}
              >
                스마트스토어<br />
                <span style={{ fontSize: '0.72rem', fontWeight: 400, color: '#999' }}>마켓에만 있는 상품은 마켓에서 삭제, 삼바에만 있는 등록표시는 해제</span>
              </button>
              <button
                onClick={() => { setGhostChoiceModal(false); runElevenstGhostSyncV2() }}
                style={{
                  padding: '10px 14px', fontSize: '0.82rem', fontWeight: 600,
                  border: '1px solid #3D3D3D', borderRadius: '8px', color: '#E5E5E5',
                  background: 'rgba(255,140,80,0.15)', cursor: 'pointer', textAlign: 'left',
                }}
              >
                11번가<br />
                <span style={{ fontSize: '0.72rem', fontWeight: 400, color: '#999' }}>마켓에만 있는 상품은 마켓에서 삭제, 삼바에만 있는 등록표시는 해제</span>
              </button>
              <button
                onClick={() => { setGhostChoiceModal(false); runLotteonGhostSync() }}
                style={{
                  padding: '10px 14px', fontSize: '0.82rem', fontWeight: 600,
                  border: '1px solid #3D3D3D', borderRadius: '8px', color: '#E5E5E5',
                  background: 'rgba(255,80,140,0.15)', cursor: 'pointer', textAlign: 'left',
                }}
              >
                롯데ON<br />
                <span style={{ fontSize: '0.72rem', fontWeight: 400, color: '#999' }}>마켓에만 있는 상품은 마켓에서 삭제, 삼바에만 있는 등록표시는 해제</span>
              </button>
              <button
                onClick={() => { setGhostChoiceModal(false); runCoupangGhostSync() }}
                style={{
                  padding: '10px 14px', fontSize: '0.82rem', fontWeight: 600,
                  border: '1px solid #3D3D3D', borderRadius: '8px', color: '#E5E5E5',
                  background: 'rgba(255,200,80,0.15)', cursor: 'pointer', textAlign: 'left',
                }}
              >
                쿠팡<br />
                <span style={{ fontSize: '0.72rem', fontWeight: 400, color: '#999' }}>마켓에만 있는 상품은 마켓에서 삭제, 삼바에만 있는 등록표시는 해제</span>
              </button>
              <button
                onClick={() => setGhostChoiceModal(false)}
                style={{
                  padding: '8px 14px', fontSize: '0.78rem',
                  border: '1px solid #3D3D3D', borderRadius: '8px', color: '#888',
                  background: 'transparent', cursor: 'pointer', marginTop: '4px',
                }}
              >취소</button>
            </div>
          </div>
        </div>
      )}

      {aiJobModal && (
        <div style={{
          position: 'fixed', inset: 0, zIndex: 99998,
          background: 'rgba(0,0,0,0.7)', display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <div style={{
            background: '#1A1A1A', border: '1px solid #2D2D2D', borderRadius: '12px',
            width: '520px', maxHeight: '70vh', display: 'flex', flexDirection: 'column',
          }} onClick={e => e.stopPropagation()}>
            <div style={{
              padding: '14px 20px', borderBottom: '1px solid #2D2D2D',
              display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            }}>
              <span style={{ fontWeight: 700, fontSize: '0.9rem', color: '#E5E5E5' }}>{aiJobTitle}</span>
              {aiJobDone && (
                <button onClick={() => setAiJobModal(false)} style={{
                  background: 'none', border: 'none', color: '#888', fontSize: '0.77rem', cursor: 'pointer',
                }}>✕</button>
              )}
            </div>
            {/* 워커 다운 경고 — 30초 이상 heartbeat 끊김 */}
            {bgActiveLoaded && !bgWorkerAlive && (
              <div style={{
                padding: '8px 14px', borderBottom: '1px solid #2D2D2D',
                background: 'rgba(255,107,107,0.12)', color: '#FF6B6B',
                fontSize: '0.72rem', fontWeight: 600, display: 'flex', justifyContent: 'space-between', alignItems: 'center',
              }}>
                <span>⚠ 로컬 배경제거 워커 다운 — local_bg_worker.py 실행 필요</span>
                {bgWorkerLastSeen && (
                  <span style={{ fontSize: '0.65rem', color: '#A8A8A8', fontWeight: 400 }}>
                    last: {new Date(bgWorkerLastSeen).toLocaleTimeString('ko-KR')}
                  </span>
                )}
              </div>
            )}
            {/* 배경제거 큐 — 현재 진행/대기 중인 잡 목록 */}
            {bgActiveLoaded && bgActiveJobs.length > 0 && (
              <div style={{
                padding: '10px 14px', borderBottom: '1px solid #2D2D2D',
                background: '#0F0F0F', maxHeight: '180px', overflowY: 'auto',
              }}>
                <div style={{ fontSize: '0.72rem', color: '#FFB84D', marginBottom: '6px', fontWeight: 600 }}>
                  배경제거 큐 ({fmt(bgActiveJobs.length)}건 진행/대기)
                </div>
                {bgActiveJobs.map(j => {
                  const isRunning = j.status === 'running'
                  const pct = j.total > 0 ? Math.floor((j.current / j.total) * 100) : 0
                  return (
                    <div key={j.job_id} style={{
                      display: 'flex', alignItems: 'center', gap: '8px',
                      padding: '6px 8px', marginBottom: '4px',
                      background: '#1A1A1A', border: `1px solid ${isRunning ? '#FF8C00' : '#2D2D2D'}`,
                      borderRadius: '4px', fontSize: '0.7rem',
                    }}>
                      <span style={{
                        color: isRunning ? '#FF8C00' : '#888', fontWeight: 700, minWidth: '52px',
                      }}>{isRunning ? '▶ 진행중' : '⏸ 대기'}</span>
                      <span style={{ color: '#8A95B0', fontFamily: 'monospace' }}>{j.job_id.slice(-8)}</span>
                      <span style={{ color: '#E5E5E5', flex: 1 }}>
                        {fmt(j.current)}/{fmt(j.total)} ({fmt(pct)}%)
                      </span>
                      <button onClick={() => cancelBgJob(j.job_id)} style={{
                        padding: '3px 10px', borderRadius: '4px', fontSize: '0.65rem',
                        background: 'rgba(255,107,107,0.12)', border: '1px solid rgba(255,107,107,0.4)',
                        color: '#FF6B6B', cursor: 'pointer', fontWeight: 600,
                      }}>취소</button>
                    </div>
                  )
                })}
              </div>
            )}
            <div
              ref={aiJobLogRef}
              style={{
                flex: 1, overflow: 'auto', padding: '14px', fontFamily: 'monospace',
                fontSize: '0.68rem', lineHeight: 1.6, color: '#8A95B0',
                transform: 'scale(0.7)', transformOrigin: 'top left', width: '142.8%',
                maxHeight: '50vh',
              }}
            >
              {aiJobLogs.map((line, i) => (
                <p key={i} style={{
                  margin: 0,
                  color: line.includes('완료') && !/실패[\s:]*[1-9]/.test(line) && !/실패(?![\s:]*\d)/.test(line) ? '#51CF66'
                    : /실패[\s:]*[1-9]/.test(line) || /실패(?![\s:]*\d)/.test(line) || line.includes('오류') ? '#FF6B6B'
                    : '#8A95B0',
                }}>{fmtTextNumbers(line)}</p>
              ))}
              {!aiJobDone && (
                <p style={{ margin: 0, color: '#FFB84D' }}>처리 중...</p>
              )}
            </div>
            <div style={{ padding: '12px 20px', borderTop: '1px solid #2D2D2D', display: 'flex', justifyContent: 'flex-end', gap: '0.5rem' }}>
              {!aiJobDone && (
                <button onClick={abortAiJob} style={{
                  padding: '6px 20px', borderRadius: '6px', fontSize: '0.56rem',
                  background: 'rgba(255,107,107,0.15)', border: '1px solid rgba(255,107,107,0.4)',
                  color: '#FF6B6B', cursor: 'pointer', fontWeight: 600,
                }}>중단</button>
              )}
              {aiJobDone && (
                <button onClick={() => setAiJobModal(false)} style={{
                  padding: '6px 20px', borderRadius: '6px', fontSize: '0.56rem',
                  background: 'rgba(81,207,102,0.15)', border: '1px solid rgba(81,207,102,0.4)',
                  color: '#51CF66', cursor: 'pointer', fontWeight: 600,
                }}>확인</button>
              )}
            </div>
          </div>
        </div>
      )}

      {/* 가격재고갱신 모달 */}
      {refreshModal && (
        <div style={{
          position: 'fixed', inset: 0, zIndex: 99998,
          background: 'rgba(0,0,0,0.7)', display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <div style={{
            background: '#1A1A1A', border: '1px solid #2D2D2D', borderRadius: '12px',
            width: '860px', maxHeight: '70vh', display: 'flex', flexDirection: 'column',
          }} onClick={e => e.stopPropagation()}>
            <div style={{
              padding: '14px 20px', borderBottom: '1px solid #2D2D2D',
              display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            }}>
              <span style={{ fontWeight: 700, fontSize: '0.9rem', color: '#E5E5E5' }}>가격재고갱신</span>
              {!refreshLoading && (
                <button onClick={() => setRefreshModal(false)} style={{
                  background: 'none', border: 'none', color: '#888', fontSize: '0.77rem', cursor: 'pointer',
                }}>✕</button>
              )}
            </div>
            <div style={{ flex: 1, overflow: 'auto', padding: '0', maxHeight: '50vh' }}>
              {refreshLoading ? (
                <div style={{ padding: '40px 20px', textAlign: 'center', color: '#FFB84D', fontSize: '0.85rem' }}>
                  갱신 중... ({fmt(selectedIds.size)}건)
                </div>
              ) : (
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.75rem' }}>
                  <thead>
                    <tr style={{ borderBottom: '1px solid #2D2D2D', color: '#888' }}>
                      <th style={{ padding: '8px 12px', textAlign: 'left', fontWeight: 500 }}>시간</th>
                      <th style={{ padding: '8px 12px', textAlign: 'left', fontWeight: 500 }}>브랜드</th>
                      <th style={{ padding: '8px 12px', textAlign: 'left', fontWeight: 500 }}>상품명</th>
                      <th style={{ padding: '8px 12px', textAlign: 'left', fontWeight: 500 }}>변동</th>
                    </tr>
                  </thead>
                  <tbody>
                    {refreshDetails.map((d, i) => (
                      <tr key={i} style={{ borderBottom: '1px solid #1E1E1E' }}>
                        <td style={{ padding: '6px 12px', color: '#888', whiteSpace: 'nowrap' }}>{d.time}</td>
                        <td style={{ padding: '6px 12px', color: '#B0B0B0', whiteSpace: 'nowrap' }} title={d.brand}>{d.brand}</td>
                        <td style={{ padding: '6px 12px', color: '#E5E5E5', whiteSpace: 'nowrap' }} title={d.name}>{d.name}</td>
                        <td style={{
                          padding: '6px 12px', whiteSpace: 'nowrap',
                          color: d.status === 'changed' ? '#51CF66' : d.status === 'error' ? '#FF6B6B' : '#666',
                        }}>
                          {d.detail}
                          {d.retransmitted && (
                            <span style={{ marginLeft: '8px', color: '#4DABF7', fontSize: '0.7rem' }}>→재전송</span>
                          )}
                        </td>
                      </tr>
                    ))}
                    {refreshDetails.length === 0 && !refreshLoading && (
                      <tr><td colSpan={4} style={{ padding: '20px', textAlign: 'center', color: '#666' }}>결과 없음</td></tr>
                    )}
                  </tbody>
                </table>
              )}
            </div>
            {refreshSummary && !refreshLoading && (
              <div style={{
                padding: '10px 20px', borderTop: '1px solid #2D2D2D',
                fontSize: '0.75rem', color: '#B0B0B0',
                display: 'flex', justifyContent: 'space-between', alignItems: 'center',
              }}>
                <span>{refreshSummary}</span>
                <button onClick={() => setRefreshModal(false)} style={{
                  padding: '5px 16px', borderRadius: '6px', fontSize: '0.75rem',
                  background: 'rgba(81,207,102,0.15)', border: '1px solid rgba(81,207,102,0.4)',
                  color: '#51CF66', cursor: 'pointer', fontWeight: 600,
                }}>확인</button>
              </div>
            )}
          </div>
        </div>
      )}

      {/* 삭제 확인 모달 */}
      {deleteConfirm && (
        <div
          style={{ position: "fixed", inset: 0, zIndex: 99999, background: "rgba(0,0,0,0.75)", display: "flex", alignItems: "center", justifyContent: "center" }}
          onClick={() => setDeleteConfirm(null)}
        >
          <div
            style={{ background: "#1A1A1A", border: "1px solid #2D2D2D", borderRadius: "12px", padding: "28px 32px", minWidth: "320px", maxWidth: "480px" }}
            onClick={(e) => e.stopPropagation()}
          >
            <h3 style={{ margin: "0 0 8px", fontSize: "1rem", fontWeight: 600, color: "#E5E5E5" }}>상품 삭제</h3>
            <p style={{ margin: "0 0 24px", fontSize: "0.875rem", color: "#888", lineHeight: 1.6 }}>
              {deleteConfirm.label}을(를) 삭제하시겠습니까?<br />
              <span style={{ color: "#FF6B6B", fontSize: "0.8rem" }}>삭제된 상품은 복구할 수 없습니다.</span>
            </p>
            <div style={{ display: "flex", justifyContent: "flex-end", gap: "8px" }}>
              <button
                onClick={() => setDeleteConfirm(null)}
                style={{ padding: "7px 20px", fontSize: "0.85rem", borderRadius: "6px", cursor: "pointer", border: "1px solid #3D3D3D", background: "transparent", color: "#888" }}
              >취소</button>
              <button
                onClick={confirmDelete}
                style={{ padding: "7px 20px", fontSize: "0.85rem", borderRadius: "6px", cursor: "pointer", border: "1px solid rgba(255,107,107,0.5)", background: "rgba(255,107,107,0.15)", color: "#FF6B6B", fontWeight: 600 }}
              >삭제</button>
            </div>
          </div>
        </div>
      )}
      {/* AI 태그 미리보기 모달 */}
      {showTagPreview && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 99999, background: 'rgba(0,0,0,0.75)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}
          onClick={() => { setShowTagPreview(false); setRemovedTags([]) }}>
          <div style={{ background: '#1A1A1A', border: '1px solid #2D2D2D', borderRadius: '12px', padding: '28px 32px', minWidth: '500px', maxWidth: '700px', maxHeight: '80vh', overflowY: 'auto' }}
            onClick={(e) => e.stopPropagation()}>
            <h3 style={{ margin: '0 0 4px', fontSize: '1rem', fontWeight: 600, color: '#E5E5E5' }}>AI 태그 미리보기</h3>
            <p style={{ margin: '0 0 20px', fontSize: '0.75rem', color: '#888' }}>
              태그사전에 미등록된 태그를 X로 제거한 후 적용하세요
            </p>
            {tagPreviews.map((preview) => (
              <div key={preview.group_id} style={{ marginBottom: '20px', padding: '16px', background: '#0F0F0F', borderRadius: '8px', border: '1px solid #2D2D2D' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '4px' }}>
                  <span style={{ fontSize: '0.82rem', color: '#FFB84D', fontWeight: 600 }}>{preview.rep_name}</span>
                  <span style={{ fontSize: '0.7rem', color: '#666' }}>{fmt(preview.product_count)}개 상품 | {fmt(preview.tags.length)}개 태그</span>
                </div>
                <div style={{ marginBottom: '10px', display: 'flex', alignItems: 'center', gap: '6px' }}>
                  <span style={{ fontSize: '0.72rem', color: '#4C9AFF', fontWeight: 600, whiteSpace: 'nowrap' }}>SEO:</span>
                  <input
                    type="text"
                    defaultValue={preview.seo_keywords.join(', ')}
                    placeholder="SEO 키워드 (콤마 구분)"
                    onBlur={(e) => {
                      const newKws = e.target.value.split(',').map(s => s.trim()).filter(Boolean)
                      setTagPreviews(prev => prev.map(p =>
                        p.group_id === preview.group_id ? { ...p, seo_keywords: newKws } : p
                      ))
                    }}
                    onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur() }}
                    style={{ flex: 1, fontSize: '0.72rem', padding: '3px 8px', background: '#1A1A1A', border: '1px solid #2D2D2D', borderRadius: '4px', color: '#4C9AFF', outline: 'none' }}
                  />
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', marginBottom: '6px' }}>
                  {preview.tags.map((tag, ti) => (
                    <span key={ti} style={{
                      fontSize: '0.78rem', padding: '4px 10px', borderRadius: '14px',
                      background: 'rgba(100,100,255,0.1)', border: '1px solid rgba(100,100,255,0.25)', color: '#8B8FD4',
                      display: 'inline-flex', alignItems: 'center', gap: '6px',
                    }}>
                      {tag}
                      <span
                        style={{ cursor: 'pointer', color: '#666', fontSize: '0.85rem', lineHeight: 1 }}
                        onClick={async () => {
                          setTagPreviews(prev => prev.map(p => ({
                            ...p, tags: p.tags.filter(t => t !== tag)
                          })))
                          const ban = await showConfirm(`"${tag}"을(를) 금지태그에 등록할까요?\n(등록하면 다음 AI태그 생성 시 자동 제외됩니다)`)
                          if (ban) {
                            setRemovedTags(prev => prev.includes(tag) ? prev : [...prev, tag])
                          }
                        }}
                      >&times;</span>
                    </span>
                  ))}
                </div>
                <input
                  type="text"
                  placeholder="추가 태그 입력 후 Enter (콤마 구분 가능)"
                  onKeyDown={e => {
                    if (e.key === 'Enter') {
                      const input = (e.target as HTMLInputElement)
                      const newTags = input.value.split(',').map(t => t.trim()).filter(Boolean)
                      if (newTags.length === 0) return
                      setTagPreviews(prev => prev.map(p =>
                        p.group_id === preview.group_id
                          ? { ...p, tags: [...p.tags, ...newTags.filter(t => !p.tags.includes(t))] }
                          : p
                      ))
                      input.value = ''
                    }
                  }}
                  style={{
                    width: '100%', padding: '5px 10px', fontSize: '0.75rem',
                    background: '#111', border: '1px solid #2D2D2D', borderRadius: '6px',
                    color: '#E5E5E5', outline: 'none',
                  }}
                />
              </div>
            ))}
            {removedTags.length > 0 && (
              <div style={{ marginBottom: '12px', padding: '10px 14px', background: 'rgba(255,107,107,0.06)', borderRadius: '6px', border: '1px solid rgba(255,107,107,0.15)' }}>
                <span style={{ fontSize: '0.72rem', color: '#FF6B6B', fontWeight: 600 }}>금지태그 등록 예정 ({fmt(removedTags.length)}개): </span>
                <span style={{ fontSize: '0.72rem', color: '#888' }}>{removedTags.join(', ')}</span>
              </div>
            )}
            {tagPreviewCost && (
              <p style={{ margin: '0 0 16px', fontSize: '0.72rem', color: '#666', textAlign: 'right' }}>
                API {fmt(tagPreviewCost.api_calls)}회 | {fmt(tagPreviewCost.input_tokens + tagPreviewCost.output_tokens)} 토큰 | ~{fmt(tagPreviewCost.cost_krw)}원
              </p>
            )}
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px' }}>
              <button onClick={() => { setShowTagPreview(false); setRemovedTags([]) }}
                style={{ padding: '7px 20px', fontSize: '0.85rem', borderRadius: '6px', cursor: 'pointer', border: '1px solid #3D3D3D', background: 'transparent', color: '#888' }}>취소</button>
              <button onClick={async () => {
                const groups = tagPreviews.filter(p => p.tags.length > 0).map(p => ({ group_id: p.group_id, tags: p.tags, seo_keywords: p.seo_keywords, coupang_search_tags: (p as { coupang_search_tags?: string[] }).coupang_search_tags || [] }))
                if (groups.length === 0) { showAlert('적용할 태그가 없습니다'); return }
                try {
                  const res = await proxyApi.applyAiTags(groups, removedTags)
                  if (res.success) {
                    showAlert(res.message, 'success')
                    if (tagPreviewCost) {
                      const now = new Date().toLocaleTimeString('ko-KR', { hour12: false, hour: '2-digit', minute: '2-digit' })
                      setLastAiUsage({
                        calls: tagPreviewCost.api_calls,
                        tokens: tagPreviewCost.input_tokens + tagPreviewCost.output_tokens,
                        cost: tagPreviewCost.cost_krw,
                        date: now,
                      })
                    }
                    setShowTagPreview(false)
                    setSelectedIds(new Set()); setSelectAll(false)
                    // 태그 로컬 반영 — product_ids 배열로 매핑해 그룹 내 모든 상품에 반영
                    const productTagMap = new Map<string, { tags: string[]; seo: string[] }>()
                    tagPreviews.forEach(tp => {
                      tp.product_ids?.forEach(pid => {
                        productTagMap.set(pid, { tags: tp.tags, seo: tp.seo_keywords })
                      })
                    })
                    setAllProducts(prev => prev.map(pp => {
                      const entry = productTagMap.get(pp.id)
                      if (!entry) return pp
                      const existing = (pp.tags || []).filter(t => t.startsWith('__'))
                      return { ...pp, tags: [...existing, '__ai_tagged__', ...entry.tags], seo_keywords: entry.seo } as SambaCollectedProduct
                    }))
                  } else showAlert(res.message, 'error')
                } catch (e) {
                  showAlert(`태그 적용 실패: ${e instanceof Error ? e.message : '알 수 없는 오류'}`, 'error')
                }
              }}
                style={{ padding: '7px 20px', fontSize: '0.85rem', borderRadius: '6px', cursor: 'pointer', border: '1px solid rgba(255,140,0,0.5)', background: 'rgba(255,140,0,0.15)', color: '#FF8C00', fontWeight: 600 }}>
                전체 그룹에 적용 ({fmt(tagPreviews.reduce((s, p) => s + p.tags.length, 0))}개 태그)
              </button>
            </div>
          </div>
        </div>
      )}
      {/* 그룹 필터 배지 */}
      {filterByGroupId && (
        <div style={{
          display: "flex", alignItems: "center", gap: "8px",
          padding: "6px 12px", marginBottom: "12px", borderRadius: "8px",
          background: "rgba(255,140,0,0.08)", border: "1px solid rgba(255,140,0,0.3)",
          fontSize: "0.82rem",
        }}>
          <span style={{ color: "#888" }}>검색그룹:</span>
          <span style={{
            color: "#FF8C00", fontWeight: 600,
            background: "rgba(255,140,0,0.12)", border: "1px solid rgba(255,140,0,0.4)",
            padding: "1px 8px", borderRadius: "4px",
          }}>
            {filterGroupName || filterByGroupId}
          </span>
          <button
            onClick={() => { setFilterByGroupId(""); setFilterGroupName("") }}
            style={{
              marginLeft: "auto", background: "transparent", border: "1px solid #3D3D3D",
              color: "#888", padding: "2px 10px", borderRadius: "4px",
              fontSize: "0.75rem", cursor: "pointer",
            }}
          >
            ✕ 해제
          </button>
        </div>
      )}
      {/* 상품 하이라이트 필터 배지 */}
      {highlightProductId && (
        <div style={{
          display: "flex", alignItems: "center", gap: "8px",
          padding: "6px 12px", marginBottom: "12px", borderRadius: "8px",
          background: "rgba(76,154,255,0.08)", border: "1px solid rgba(76,154,255,0.3)",
          fontSize: "0.82rem",
        }}>
          <span style={{ color: "#888" }}>선택 상품:</span>
          <span style={{ color: "#4C9AFF", fontWeight: 600 }}>
            {allProducts.find(p => p.id === highlightProductId)?.name?.slice(0, 40) || highlightProductId}
          </span>
          <button
            onClick={() => setHighlightProductId("")}
            style={{
              marginLeft: "auto", background: "transparent", border: "1px solid #3D3D3D",
              color: "#888", padding: "2px 10px", borderRadius: "4px",
              fontSize: "0.75rem", cursor: "pointer",
            }}
          >전체보기</button>
        </div>
      )}
      {/* KPI stat cards */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "1rem", marginBottom: "1.25rem" }}>
        <div style={{
          background: "rgba(30,30,30,0.5)", border: "1px solid #2D2D2D", borderRadius: "12px",
          padding: "1.75rem", borderLeft: "3px solid #FF8C00",
          display: "flex", flexDirection: "column", gap: "4px",
        }}>
          <p style={{ fontSize: "0.75rem", color: "#888", fontWeight: 500, letterSpacing: "0.04em", textTransform: "uppercase", margin: 0 }}>수집상품 수</p>
          <p style={{ fontSize: "1.625rem", fontWeight: 800, color: "#E5E5E5", letterSpacing: "-0.02em", margin: 0 }}>
            {fmt(kpiCounts.total)}<span style={{ fontSize: "1rem", color: "#888", fontWeight: 500 }}>개</span>
          </p>
          <p style={{ fontSize: "0.75rem", color: "#666", margin: 0 }}>등록된 상품</p>
        </div>
        <div style={{
          background: "rgba(30,30,30,0.5)", border: "1px solid #2D2D2D", borderRadius: "12px",
          padding: "1.75rem", borderLeft: "3px solid #FFB84D",
          display: "flex", flexDirection: "column", gap: "4px",
        }}>
          <p style={{ fontSize: "0.75rem", color: "#888", fontWeight: 500, letterSpacing: "0.04em", textTransform: "uppercase", margin: 0 }}>판매상품 수</p>
          <p style={{ fontSize: "1.625rem", fontWeight: 800, color: "#51CF66", letterSpacing: "-0.02em", margin: 0 }}>
            {fmt(registeredCount)}<span style={{ fontSize: "1rem", color: "#888", fontWeight: 500 }}>개</span>
          </p>
          <p style={{ fontSize: "0.75rem", color: "#666", margin: 0 }}>판매중인 상품</p>
        </div>
      </div>

      {/* Filter area */}
      <div style={{
        background: "rgba(30,30,30,0.5)", border: "1px solid #2D2D2D", borderRadius: "8px",
        padding: "1rem", marginBottom: "1rem", fontSize: "0.875rem",
      }}>
        {/* 검색 조건 1줄 배치 */}
        <div style={{ display: "flex", alignItems: "center", gap: "6px", flexWrap: "wrap" }}>
          <span style={{ color: "#888", whiteSpace: "nowrap", fontSize: "0.8125rem" }}>등록일자</span>
          <input type="date" style={{
            width: "130px", padding: "0.3rem 0.4rem", fontSize: "0.78rem",
            background: "rgba(30,30,30,0.5)", border: "1px solid #2D2D2D", borderRadius: "6px",
            color: "#E5E5E5",
          }} />
          <span style={{ color: "#888" }}>~</span>
          <input type="date" style={{
            width: "130px", padding: "0.3rem 0.4rem", fontSize: "0.78rem",
            background: "rgba(30,30,30,0.5)", border: "1px solid #2D2D2D", borderRadius: "6px",
            color: "#E5E5E5",
          }} />
          <select value={siteFilter} onChange={(e) => setSiteFilter(e.target.value)}
            style={{ padding: "0.3rem 0.4rem", fontSize: "0.78rem", background: "rgba(22,22,22,0.95)", border: "1px solid #353535", color: "#C5C5C5", borderRadius: "6px" }}>
            <option value="">소싱사이트</option>
            {allSites.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
          <select value={soldOutFilter} onChange={(e) => setSoldOutFilter(e.target.value)}
            style={{ padding: "0.3rem 0.4rem", fontSize: "0.78rem", background: "rgba(22,22,22,0.95)", border: "1px solid #353535", color: "#C5C5C5", borderRadius: "6px" }}>
            <option value="">품절여부</option>
            <option value="sold_out">품절</option>
            <option value="not_sold_out">비품절</option>
          </select>
          <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}
            style={{ padding: "0.3rem 0.4rem", fontSize: "0.78rem", background: "rgba(22,22,22,0.95)", border: "1px solid #353535", color: "#C5C5C5", borderRadius: "6px" }}>
            <option value="">마켓현황</option>
            <option value="market_unregistered">미등록상품</option>
            <option value="market_registered">등록상품</option>
            {[...new Map(accounts.map(a => [a.market_type, a.market_name] as const)).entries()].map(([type, name]) => (
              <React.Fragment key={type}>
                <option value={`mtype_reg_${type}`}>{name} 등록</option>
                <option value={`mtype_unreg_${type}`}>{name} 미등록</option>
              </React.Fragment>
            ))}
            {[...accounts].sort((a, b) => a.market_type.localeCompare(b.market_type)).map(a => (
              <React.Fragment key={a.id}>
                <option value={`reg_${a.id}`}>{a.market_name}({a.account_label}) 등록</option>
                <option value={`unreg_${a.id}`}>{a.market_name}({a.account_label}) 미등록</option>
              </React.Fragment>
            ))}
          </select>
          <select value={searchType} onChange={(e) => setSearchType(e.target.value)}
            style={{ padding: "0.3rem 0.4rem", fontSize: "0.78rem", background: "#1E1E1E", border: "1px solid #3D3D3D", borderRadius: "6px", color: "#C5C5C5", width: "90px" }}>
            <option value="name">검색항목</option>
            <option value="brand">브랜드</option>
            <option value="name_all">상품명</option>
            <option value="filter">그룹</option>
            <option value="no">상품번호</option>
            <option value="policy">정책</option>
          </select>
          <input type="text" placeholder={searchType === "no" ? "상품번호 검색 (콤마로 다중)" : "검색어"} value={searchQ}
            onChange={(e) => setSearchQ(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSearch()}
            style={{
              flex: 1, minWidth: "120px", maxWidth: "200px",
              padding: "0.3rem 0.5rem", fontSize: "0.78rem",
              background: "#1E1E1E", border: "1px solid #3D3D3D", borderRadius: "6px",
              color: "#C5C5C5", outline: "none",
            }}
          />
          <button onClick={handleSearch}
            style={{
              background: "rgba(255,140,0,0.15)", border: "1px solid #FF8C00",
              color: "#FF8C00", padding: "0.3rem 0.625rem", borderRadius: "6px",
              fontSize: "0.78rem", whiteSpace: "nowrap", flexShrink: 0, cursor: "pointer",
            }}>검색</button>
        </div>
      </div>

      {/* 작업 로그 패널 */}
      {taskLogs.length > 0 && (<div style={{ background: 'rgba(8,10,16,0.98)', border: '1px solid #1C1E2A', borderRadius: '8px', marginBottom: '8px', overflow: 'hidden' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '6px 14px', background: '#0A0D14', borderBottom: '1px solid #1C1E2A' }}>
          <span style={{ fontSize: '0.78rem', fontWeight: 600, color: '#9AA5C0' }}>작업 로그</span>
          <div style={{ display: 'flex', gap: '6px' }}>
            <button onClick={() => navigator.clipboard.writeText(taskLogs.join('\n'))} style={{ padding: '2px 8px', fontSize: '0.68rem', background: 'transparent', border: '1px solid #252B3B', color: '#666', borderRadius: '3px', cursor: 'pointer' }}>복사</button>
            <button onClick={() => setTaskLogs([])} style={{ padding: '2px 8px', fontSize: '0.68rem', background: 'transparent', border: '1px solid #252B3B', color: '#666', borderRadius: '3px', cursor: 'pointer' }}>초기화</button>
          </div>
        </div>
        <div ref={el => { if (el) el.scrollTop = el.scrollHeight }} style={{ maxHeight: '150px', overflowY: 'auto', padding: '8px 14px', fontFamily: "'Courier New', monospace", fontSize: '0.72rem', lineHeight: 1.7 }}>
          {taskLogs.map((msg, i) => {
            let color = '#555'
            if (/실패[\s:]*[1-9]/.test(msg) || /실패(?![\s:]*\d)/.test(msg) || msg.includes('오류')) color = '#FF6B6B'
            else if (msg.includes('완료') || msg.includes('성공')) color = '#51CF66'
            else if (msg.includes('생성 중') || msg.includes('처리 중')) color = '#FFB84D'
            return <div key={i} style={{ color }}>{msg}</div>
          })}
        </div>
      </div>)}

      {/* AI비용 + AI 이미지 변환 + 이미지 필터링 — 3단 나란히 배치 */}
      <div style={{ display: 'grid', gridTemplateColumns: '0.7fr 1.3fr 1fr', gap: '8px', marginBottom: '1rem' }}>
      {/* AI 비용 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.625rem', padding: '0.5rem 1rem', background: 'rgba(81,207,102,0.08)', border: '1px solid rgba(81,207,102,0.2)', borderRadius: '8px', flexWrap: 'wrap' }}>
        <span style={{ fontSize: '0.8125rem', color: '#51CF66', fontWeight: 600 }}>AI 비용</span>
        {lastAiUsage ? (
          <>
            <span style={{ fontSize: '0.78rem', color: '#E5E5E5' }}>{fmt(lastAiUsage.calls)}건</span>
            <span style={{ fontSize: '0.78rem', color: '#888' }}>·</span>
            <span style={{ fontSize: '0.78rem', color: '#FFB84D' }}>₩{fmt(lastAiUsage.cost)}</span>
            <span style={{ fontSize: '0.7rem', color: '#555' }}>{lastAiUsage.date}</span>
          </>
        ) : (
          <span style={{ fontSize: '0.78rem', color: '#555' }}>사용 내역 없음</span>
        )}
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.625rem', padding: '0.5rem 1rem', background: 'rgba(255,140,0,0.08)', border: '1px solid rgba(255,140,0,0.2)', borderRadius: '8px', flexWrap: 'wrap' }}>
        <span style={{ fontSize: '0.8125rem', color: '#FF8C00', fontWeight: 600 }}>AI 이미지 변환</span>
        {([['thumbnail', '대표'], ['additional', '추가'], ['detail', '상세']] as const).map(([key, label]) => (
          <label key={key} style={{ display: 'flex', alignItems: 'center', gap: '4px', cursor: 'pointer' }}>
            <input type="checkbox" checked={aiImgScope[key]}
              onChange={() => setAiImgScope(prev => ({ ...prev, [key]: !prev[key] }))}
              style={{ accentColor: '#FF8C00', width: '13px', height: '13px' }} />
            <span style={{ fontSize: '0.78rem', color: '#E5E5E5' }}>{label}</span>
          </label>
        ))}
        <select value={aiImgMode} onChange={e => setAiImgMode(e.target.value)} style={{ background: '#1A1A1A', border: '1px solid #333', color: '#E5E5E5', borderRadius: '4px', padding: '2px 6px', fontSize: '0.78rem' }}>
          <option value="background">배경 제거</option>
          <option value="model_to_product">모델→상품</option>
          <option value="scene">연출컷</option>
          <option value="model">모델 착용</option>
        </select>
        {aiImgMode === 'model' && (
          <select
            value={aiModelPreset}
            onChange={e => setAiModelPreset(e.target.value)}
            style={{ background: '#1A1A1A', border: '1px solid #333', color: '#E5E5E5', borderRadius: '4px', padding: '2px 6px', fontSize: '0.78rem' }}
          >
            <option value="auto">자동 (성별·연령 판별)</option>
            {['여성', '남성', '키즈 여아', '키즈 남아'].map(group => {
              const groupPresets = aiPresetList.filter(p => {
                if (group === '여성') return p.key.startsWith('female_')
                if (group === '남성') return p.key.startsWith('male_')
                if (group === '키즈 여아') return p.key.startsWith('kids_girl_')
                return p.key.startsWith('kids_boy_')
              })
              if (!groupPresets.length) return null
              return (
                <optgroup key={group} label={group}>
                  {groupPresets.map(p => (
                    <option key={p.key} value={p.key}>{p.label.replace(/^.*—\s*/, '')}</option>
                  ))}
                </optgroup>
              )
            })}
          </select>
        )}
        <span style={{ fontSize: '0.78rem', color: '#888' }}>({fmt(selectedIds.size)}개 상품)</span>
        <button
          onClick={async () => {
            if (selectedIds.size === 0) { showAlert('상품을 선택해주세요'); return }
            if (!aiImgScope.thumbnail && !aiImgScope.additional && !aiImgScope.detail) { showAlert('변환 대상 이미지를 선택해주세요 (대표/추가/상세)'); return }
            const scopeLabel = [aiImgScope.thumbnail && '대표', aiImgScope.additional && '추가', aiImgScope.detail && '상세'].filter(Boolean).join('+')
            const ok = await showConfirm(`선택된 ${fmt(selectedIds.size)}개 상품의 ${scopeLabel} 이미지를 변환하시겠습니까?`)
            if (!ok) return
            const ids = [...selectedIds]
            const ts = fmtTime
            setAiImgTransforming(true)
            aiJobAbortRef.current = false
            setAiJobTitle(`AI 이미지변환 (${fmt(ids.length)}개)`)
            setAiJobLogs([])
            setAiJobDone(false)
            setAiJobModal(true)
            const addLog = (msg: string) => setAiJobLogs(prev => [...prev, msg])
            // allProducts에 없는 상품 정보 미리 로드 (500개씩 청크)
            const missingIds = ids.filter(id => !allProducts.find(p => p.id === id))
            const productMap: Record<string, typeof allProducts[0]> = {}
            allProducts.forEach(p => { productMap[p.id] = p })
            for (let ci = 0; ci < missingIds.length; ci += 500) {
              try {
                const chunk = missingIds.slice(ci, ci + 500)
                const fetched = await collectorApi.getProductsByIds(chunk)
                if (Array.isArray(fetched)) fetched.forEach(p => { productMap[p.id] = p })
              } catch { /* 조회 실패 시 기존 fallback */ }
            }
            const startTime = ts()
            addLog(`시작: ${startTime} (${fmt(ids.length)}개 상품)`)
            let success = 0
            let fail = 0
            if (aiImgMode === 'background') {
              // 워커가 죽어있으면 → 자동 설치/재기동 안내 후 다운로드 트리거
              if (bgActiveLoaded && !bgWorkerAlive) {
                setAiImgTransforming(false)
                setAiJobModal(false)
                const goInstall = await showConfirm(
                  '배경제거 워커가 실행되지 않습니다.\n\n' +
                  '[확인]을 누르면 설치 패키지(samba-bg-worker.zip)가 다운로드됩니다.\n' +
                  '1) ZIP 압축 해제 → 2) install.bat 더블클릭 → 끝.\n\n' +
                  '자동 등록되어 PC 재부팅 후에도 자동 실행되며,\n' +
                  '워커가 죽으면 1분 안에 자동 부활합니다.\n' +
                  '(Python 미설치 시 install.bat 이 자동 설치 시도)'
                )
                if (goInstall) {
                  const { API_BASE_URL: apiBase } = await import('@/config/api')
                  window.location.href = `${apiBase}/api/v1/samba/proxy/bg-jobs/installer`
                }
                return
              }
              // 배경제거: 백엔드 job queue 일괄 제출 + 폴링
              addLog(`[${ts()}] 배경 제거 큐 제출 중... (${fmt(ids.length)}개 상품)`)
              try {
                let batchRes: Awaited<ReturnType<typeof proxyApi.transformImages>> | null = null
                for (let attempt = 0; attempt <= 2; attempt++) {
                  if (attempt > 0) {
                    const delay = attempt === 1 ? 2000 : 4000
                    addLog(`[${ts()}] 큐 등록 재시도 ${attempt}/2 (${delay / 1000}초 후)...`)
                    await new Promise(r => setTimeout(r, delay))
                  }
                  try { batchRes = await proxyApi.transformImages(ids, aiImgScope, 'background'); break }
                  catch { if (attempt === 2) throw new Error('Failed to fetch') }
                }
                const batchResVal = batchRes!
                if (!batchResVal.success || !batchResVal.job_id) {
                  fail = ids.length
                  addLog(`큐 등록 실패: ${batchResVal.message}`)
                } else {
                  const jid = batchResVal.job_id
                  addLog(`[${ts()}] 큐 등록 완료 (job: ${jid.slice(-8)}) — 로컬 워커 처리 대기 중...`)
                  addLog(`※ 로컬 워커(local_bg_worker.py)가 실행 중이어야 처리됩니다`)
                  let pollCount = 0
                  // 큰 잡에서도 안 끊기도록 잡 크기에 비례 — 잡당 최대 5분 + 여유 30분, 24h 캡
                  const maxPolls = Math.min(Math.max(720, ids.length * 60 + 360), 17280)
                  let lastLoggedPid = ''
                  let lastLoggedCur = -1
                  let lastImgLogPoll = 0  // 이미지 진행 로그(상품 정체 시) 마지막 추가 pollCount
                  let lastImgCur = -1
                  while (pollCount < maxPolls && !aiJobAbortRef.current) {
                    await new Promise(r => setTimeout(r, 5000))
                    pollCount++
                    try {
                      const st = await proxyApi.bgJobStatus(jid)
                      const cur = st.current ?? 0
                      const tot = st.total ?? ids.length
                      const imgCur = st.image_current ?? 0
                      const imgTot = st.image_total ?? 0
                      const stPid = st.current_product_id || ''
                      // 진행률은 모달 타이틀에만 표시 — 로그는 상품 단위 1줄만
                      const titleProgress = imgTot > 0
                        ? ` (${fmt(Math.max(imgCur, 0))}/${fmt(imgTot)}장)`
                        : ''
                      setAiJobTitle(`배경제거 [${fmt(Math.min(cur + 1, tot))}/${fmt(tot)}]${titleProgress}`)
                      // pending 상태 감지 — 워커 자체가 죽었을 때만 경고 (다른 잡 처리 중이면 heartbeat 신선해서 bgWorkerAlive=true)
                      if (st.status === 'pending' && bgActiveLoaded && !bgWorkerAlive) {
                        if (pollCount === 6) addLog(`[${ts()}] ⚠️ 로컬 워커가 응답하지 않습니다 — 워치독이 1분 안에 자동 부활합니다`)
                        if (pollCount === 18) addLog(`[${ts()}] ❌ 워커 부활 실패 — install.bat 재실행 필요할 수 있음`)
                      }
                      // 새 상품 진입 시점에 1줄 로그 — pid 변경 또는 cur 증가 둘 중 하나만 되어도 로그
                      const pidChanged = !!stPid && stPid !== lastLoggedPid
                      const curAdvanced = cur > lastLoggedCur
                      if (st.status === 'running' && (pidChanged || curAdvanced)) {
                        const curProd = productMap[stPid]
                          || allProducts.find(p => p.id === stPid)
                          || allProducts.find(p => p.site_product_id === stPid)
                        const curBrand = curProd?.brand || ''
                        const curName = (curProd?.name || '').slice(0, 30)
                        const curNo = curProd?.site_product_id || stPid.slice(-8)
                        const label = [curBrand, curName, curNo].filter(Boolean).join(' / ')
                        const totalImg = imgTot > 0 ? ` — ${fmt(imgTot)}장` : ''
                        addLog(`[${ts()}] [${fmt(Math.min(cur + 1, tot))}/${fmt(tot)}] ${label}${totalImg}`)
                        lastLoggedPid = stPid
                        lastLoggedCur = cur
                        lastImgLogPoll = pollCount
                        lastImgCur = imgCur
                      } else if (
                        st.status === 'running'
                        && imgCur > lastImgCur
                        && pollCount - lastImgLogPoll >= 6  // 같은 상품 처리 30초 이상 정체 시
                        && imgTot > 0
                      ) {
                        // 한 상품에서 이미지 처리가 길어질 때 진행 표시 (rembg 폴백 등)
                        addLog(`[${ts()}] ⏳ 처리 중 — ${fmt(imgCur)}/${fmt(imgTot)}장`)
                        lastImgLogPoll = pollCount
                        lastImgCur = imgCur
                      }
                      if (st.status === 'completed') {
                        success = st.total_transformed || 0
                        fail = st.total_failed || 0
                        addLog(`[${ts()}] 완료 — 성공 ${fmt(success)}개 / 실패 ${fmt(fail)}개`)
                        break
                      }
                      if (st.status === 'failed' || st.status === 'not_found') {
                        fail = ids.length
                        addLog(`[${ts()}] 워커 처리 실패`)
                        break
                      }
                      if (st.status === 'cancelled') {
                        success = st.total_transformed || 0
                        fail = (st.total ?? ids.length) - success
                        addLog(`[${ts()}] 잡 취소됨 (워커 재시작 또는 사용자 취소) — 처리: ${fmt(success)}/${fmt(ids.length)}`)
                        break
                      }
                    } catch { /* 폴링 오류 무시 */ }
                  }
                  if (aiJobAbortRef.current) addLog(`⛔ 사용자 중단`)
                  else if (pollCount >= maxPolls) { addLog(`타임아웃 — 잡 크기 대비 한도 초과`); fail = ids.length - success }
                }
              } catch (e) {
                fail = ids.length
                addLog(`오류: ${e instanceof Error ? e.message : ''}`)
              }
            } else {
              for (let i = 0; i < ids.length; i++) {
                if (aiJobAbortRef.current) { addLog(`\n⛔ 사용자 중단 (${fmt(i)}/${fmt(ids.length)})`); break }
                const prod = productMap[ids[i]] || allProducts.find(p => p.id === ids[i])
                const brand = prod?.brand || ''
                const name = prod?.name?.slice(0, 30) || ''
                const prodNo = prod?.site_product_id || ids[i].slice(-8)
                const label = [brand, name, prodNo].filter(Boolean).join(' / ')
                setAiJobTitle(`AI 이미지변환 [${fmt(i + 1)}/${fmt(ids.length)}] ${label}`)
                const delays = [3000, 5000]
                for (let attempt = 0; attempt <= 2; attempt++) {
                  if (attempt > 0) {
                    addLog(`[${ts()}] [${fmt(i + 1)}/${fmt(ids.length)}] ${label} — 재시도 ${attempt}/2`)
                    await new Promise(r => setTimeout(r, delays[attempt - 1]))
                  }
                  try {
                    const res = await proxyApi.transformImages([ids[i]], aiImgScope, aiImgMode, aiModelPreset)
                    if (res.success && res.total_transformed > 0) {
                      success++; addLog(`[${ts()}] [${fmt(i + 1)}/${fmt(ids.length)}] ${label} — 완료 (${fmt(res.total_transformed)}장)`)
                    } else {
                      fail++; addLog(`[${ts()}] [${fmt(i + 1)}/${fmt(ids.length)}] ${label} — 실패: ${res.message || '변환된 이미지 0장'}`)
                    }
                    break
                  } catch (e) {
                    if (attempt === 2) { fail++; addLog(`[${ts()}] [${fmt(i + 1)}/${fmt(ids.length)}] ${label} — 오류: ${e instanceof Error ? e.message : ''}`) }
                  }
                }
              }
            }
            const endTime = ts()
            setAiJobTitle(`AI 이미지변환 완료 (${fmt(success)}/${fmt(ids.length)})`)
            addLog(`\n완료: 성공 ${fmt(success)}개 / 실패 ${fmt(fail)}개`)
            addLog(`시작 ${startTime} → 종료 ${endTime}`)
            setAiJobDone(true)
            setAiImgTransforming(false)
            setSelectedIds(new Set()); setSelectAll(false)
            reloadProducts()
          }}
          disabled={aiImgTransforming || selectedIds.size === 0}
          style={{ marginLeft: 'auto', background: aiImgTransforming ? '#333' : 'rgba(255,140,0,0.15)', border: '1px solid rgba(255,140,0,0.35)', color: aiImgTransforming ? '#888' : '#FF8C00', padding: '0.3rem 0.875rem', borderRadius: '6px', fontSize: '0.78rem', cursor: aiImgTransforming ? 'not-allowed' : 'pointer', fontWeight: 600, whiteSpace: 'nowrap' }}
        >{aiImgTransforming ? '변환중...' : '변환 실행'}</button>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: '0.625rem', padding: '0.5rem 1rem', background: 'rgba(99,102,241,0.08)', border: '1px solid rgba(99,102,241,0.2)', borderRadius: '8px', flexWrap: 'wrap' }}>
        <span style={{ fontSize: '0.8125rem', color: '#818CF8', fontWeight: 600 }}>이미지 필터링</span>
        {([['images', '대표'], ['detail_images', '추가'], ['detail', '상세']] as const).map(([key, label]) => (
          <label key={key} style={{ display: 'flex', alignItems: 'center', gap: '4px', cursor: 'pointer' }}>
            <input type="checkbox" checked={imgFilterScopes.has(key)}
              onChange={() => setImgFilterScopes(prev => {
                const next = new Set(prev)
                if (next.has(key)) next.delete(key); else next.add(key)
                return next
              })}
              style={{ accentColor: '#818CF8', width: '13px', height: '13px' }} />
            <span style={{ fontSize: '0.78rem', color: '#E5E5E5' }}>{label}</span>
          </label>
        ))}
        <button
          onClick={async () => {
            if (selectedIds.size === 0) { showAlert('상품을 선택해주세요'); return }
            if (imgFilterScopes.size === 0) { showAlert('필터링 대상을 선택해주세요'); return }
            const scopeLabel = [...imgFilterScopes].map(s => s === 'images' ? '대표' : s === 'detail_images' ? '추가' : '상세').join('+')
            const scope = imgFilterScopes.has('images') && imgFilterScopes.has('detail_images') && imgFilterScopes.has('detail') ? 'all' : imgFilterScopes.has('images') && imgFilterScopes.has('detail_images') ? 'images' : imgFilterScopes.has('detail') ? 'detail' : [...imgFilterScopes][0] || 'images'
            const ok = await showConfirm(`선택된 ${fmt(selectedIds.size)}개 상품의 ${scopeLabel} 이미지를 필터링하시겠습니까?\n(모델컷/연출컷/배너를 자동 제거합니다)`)
            if (!ok) return
            const ids = [...selectedIds]
            setImgFiltering(true)
            aiJobAbortRef.current = false
            setAiJobTitle(`이미지 필터링 (${fmt(ids.length)}개)`)
            setAiJobLogs([])
            setAiJobDone(false)
            setAiJobModal(true)
            const addLog = (msg: string) => setAiJobLogs(prev => [...prev, msg])
            const ts = fmtTime
            // allProducts에 없는 상품 정보 미리 로드 (500개씩 청크)
            const missingIds = ids.filter(id => !allProducts.find(p => p.id === id))
            const productMap: Record<string, typeof allProducts[0]> = {}
            allProducts.forEach(p => { productMap[p.id] = p })
            for (let ci = 0; ci < missingIds.length; ci += 500) {
              try {
                const chunk = missingIds.slice(ci, ci + 500)
                const fetched = await collectorApi.getProductsByIds(chunk)
                if (Array.isArray(fetched)) fetched.forEach(p => { productMap[p.id] = p })
              } catch { /* 조회 실패 시 기존 fallback */ }
            }
            let success = 0
            let fail = 0
            let totalTall = 0
            let totalVisionRemoved = 0
            const startTime = ts()
            for (let i = 0; i < ids.length; i++) {
              if (aiJobAbortRef.current) { addLog(`\n⛔ 사용자 중단 (${fmt(i)}/${fmt(ids.length)})`); break }
              const prod = productMap[ids[i]] || null
              const prodName = prod?.name?.slice(0, 25) || ids[i].slice(-8)
              const prodNo = prod?.site_product_id || ids[i].slice(-8)
              const prodBrand = prod?.brand || '-'
              const label = `${prodBrand} / ${prodNo} / ${prodName}${prod?.name && prod.name.length > 25 ? '...' : ''}`
              setAiJobTitle(`이미지 필터링 [${fmt(i + 1)}/${fmt(ids.length)}] ${prodBrand} / ${prodNo}`)
              try {
                const steps: string[] = []
                // 1) 프론트에서 추가이미지 비율 체크 (세로 2배 이상 → 제거)
                if (prod && (scope === 'detail_images' || scope === 'images' || scope === 'all')) {
                  const imgs = prod.images || []
                  if (imgs.length > 1) {
                    const tallCheck = await Promise.all(imgs.slice(1).map(url =>
                      new Promise<boolean>(resolve => {
                        const img = new window.Image()
                        img.onload = () => {
                          const isTall = img.naturalHeight > img.naturalWidth * 2
                          resolve(isTall)
                        }
                        img.onerror = () => resolve(false)
                        img.src = url
                        setTimeout(() => resolve(false), 10000)
                      })
                    ))
                    const tallUrls = imgs.slice(1).filter((_, i) => tallCheck[i])
                    if (tallUrls.length > 0) {
                      const kept = imgs.filter(u => !tallUrls.includes(u))
                      await collectorApi.updateProduct(ids[i], { images: kept })
                      totalTall += tallUrls.length
                      steps.push(`긴이미지 ${fmt(tallUrls.length)}장 제거`)
                    }
                  }
                }
                // 1-2) 프론트에서 상세이미지 비율 체크 (세로 2배 이상 → 제거)
                if (prod && (scope === 'detail' || scope === 'all')) {
                  const detailImgs = prod.detail_images || []
                  if (detailImgs.length > 0) {
                    const tallCheck = await Promise.all(detailImgs.map(url =>
                      new Promise<boolean>(resolve => {
                        const img = new window.Image()
                        img.onload = () => {
                          const isTall = img.naturalHeight > img.naturalWidth * 2
                          resolve(isTall)
                        }
                        img.onerror = () => resolve(false)
                        img.src = url
                        setTimeout(() => resolve(false), 10000)
                      })
                    ))
                    const tallUrls = detailImgs.filter((_, i) => tallCheck[i])
                    if (tallUrls.length > 0) {
                      const kept = detailImgs.filter(u => !tallUrls.includes(u))
                      await collectorApi.updateProduct(ids[i], { detail_images: kept })
                      totalTall += tallUrls.length
                      steps.push(`상세 긴이미지 ${fmt(tallUrls.length)}장 제거`)
                    }
                  }
                }
                // 2) 백엔드 이미지 필터링
                const r = await proxyApi.filterProductImages([ids[i]], '', scope)
                if (r.success) {
                  success++
                  const removed = r.total_removed || 0
                  totalVisionRemoved += removed
                  if (removed > 0) steps.push(`필터 ${removed}장 제거`)
                  else steps.push('필터 변동없음')
                  addLog(`[${ts()}] [${fmt(i + 1)}/${fmt(ids.length)}] ${label} — ${steps.join(' → ')}`)
                } else { fail++; addLog(`[${ts()}] [${fmt(i + 1)}/${fmt(ids.length)}] ${label} — ${steps.length > 0 ? steps.join(' → ') + ' → ' : ''}실패`) }
              } catch (e) { fail++; addLog(`[${ts()}] [${fmt(i + 1)}/${fmt(ids.length)}] ${label} — 오류: ${e instanceof Error ? e.message : ''}`) }
            }
            const summary = [`성공 ${fmt(success)}개`, `실패 ${fmt(fail)}개`]
            if (totalTall > 0) summary.push(`긴이미지 ${fmt(totalTall)}장 제거`)
            if (totalVisionRemoved > 0) summary.push(`필터 ${fmt(totalVisionRemoved)}장 제거`)
            setAiJobTitle(`이미지 필터링 완료 (${fmt(success)}/${fmt(ids.length)})`)
            addLog(`\n완료: ${summary.join(' / ')}`)
            addLog(`시작 ${startTime} → 종료 ${ts()}`)
            setAiJobDone(true)
            setImgFiltering(false)
            const apiCalls = success + fail
            setLastAiUsage({ calls: apiCalls, tokens: apiCalls * 1000, cost: apiCalls * 15, date: new Date().toLocaleTimeString('ko-KR', { hour12: false, hour: '2-digit', minute: '2-digit' }) })
            setSelectedIds(new Set()); setSelectAll(false)
            reloadProducts()
          }}
          disabled={imgFiltering || selectedIds.size === 0}
          style={{ marginLeft: 'auto', background: imgFiltering ? '#333' : 'rgba(99,102,241,0.15)', border: '1px solid rgba(99,102,241,0.35)', color: imgFiltering ? '#888' : '#818CF8', padding: '0.3rem 0.875rem', borderRadius: '6px', fontSize: '0.78rem', cursor: imgFiltering ? 'not-allowed' : 'pointer', fontWeight: 600, whiteSpace: 'nowrap' }}
        >{imgFiltering ? '필터링중...' : '필터링 실행'}</button>
      </div>
      </div>

      {/* Result header + action bar */}
      <div style={{
        background: "rgba(18,18,18,0.95)", border: "1px solid #2A2A2A", borderRadius: "8px",
        padding: "8px 14px", marginBottom: "1rem",
        display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: "8px",
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: "12px", flexWrap: "wrap" }}>
          <label style={{ display: "flex", alignItems: "center", gap: "5px", cursor: "pointer", margin: 0 }}>
            <input
              type="checkbox"
              checked={selectAll}
              onChange={(e) => handleSelectAll(e.target.checked)}
              style={{ accentColor: "#FF8C00", width: "13px", height: "13px", cursor: "pointer" }}
            />
          </label>
          <span style={{ fontSize: "0.875rem", color: "#E5E5E5", fontWeight: 600, whiteSpace: "nowrap" }}>
            상품관리 <span style={{ color: "#FF8C00" }}>( <span>{fmt(totalCount)}</span>개 )</span>
          </span>
          <button onClick={async () => {
            if (selectedIds.size === 0) { showAlert('상품을 선택해주세요'); return }
            const ok = await showConfirm(`선택된 ${fmt(selectedIds.size)}개 상품의 영상을 생성하시겠습니까?`)
            if (!ok) return
            for (const pid of selectedIds) {
              const prod = products.find(p => p.id === pid)
              try {
                addTaskLog(`[영상생성] ${prod?.name?.slice(0, 25) || pid} — 생성 중...`)
                await collectorApi.generateVideo(pid, 3, 1.0)
                addTaskLog(`[영상생성] ${prod?.name?.slice(0, 25) || pid} — 완료`)
              } catch (e) {
                addTaskLog(`[영상생성] ${prod?.name?.slice(0, 25) || pid} — 실패: ${e instanceof Error ? e.message : e}`)
              }
            }
            reloadProducts()
          }} style={{
            fontSize: "0.78rem", padding: "4px 12px",
            border: "1px solid #3D3D3D", borderRadius: "5px",
            color: "#B0B0B0", background: "rgba(50,50,50,0.6)", cursor: "pointer", whiteSpace: "nowrap",
          }}>영상</button>
          <button style={{
            fontSize: "0.78rem", padding: "4px 12px",
            border: "1px solid #3D3D3D", borderRadius: "5px",
            color: "#B0B0B0", background: "rgba(50,50,50,0.6)", cursor: "pointer", whiteSpace: "nowrap",
          }}>AI상품명</button>
          <button onClick={async () => {
            if (selectedIds.size === 0) { showAlert('상품을 선택해주세요'); return }
            const ok = await showConfirm(`선택된 ${fmt(selectedIds.size)}개 상품에 AI 태그를 생성하시겠습니까?\n(그룹별 대표 1개로 API 호출, 미리보기 후 확정)`)
            if (!ok) return
            setTagPreviewLoading(true)
            try {
              const res = await proxyApi.previewAiTags([...selectedIds])
              if (res.success) {
                setTagPreviews(res.previews)
                setTagPreviewCost({ api_calls: res.api_calls, input_tokens: res.input_tokens, output_tokens: res.output_tokens, cost_krw: res.cost_krw })
                setRemovedTags([])
                setShowTagPreview(true)
              } else showAlert(res.message, 'error')
            } catch (e) {
              showAlert(`태그 생성 실패: ${e instanceof Error ? e.message : '알 수 없는 오류'}`, 'error')
            } finally {
              setTagPreviewLoading(false)
            }
          }} disabled={tagPreviewLoading} style={{
            fontSize: "0.78rem", padding: "4px 12px",
            border: "1px solid #3D3D3D", borderRadius: "5px",
            color: "#B0B0B0", background: "rgba(50,50,50,0.6)", cursor: tagPreviewLoading ? "wait" : "pointer", whiteSpace: "nowrap", opacity: tagPreviewLoading ? 0.5 : 1,
          }}>{tagPreviewLoading ? 'AI태그 생성중...' : 'AI태그'}</button>
          <button onClick={async () => {
            if (selectedIds.size === 0) { showAlert('상품을 선택해주세요'); return }
            const groupIds = [...new Set(
              [...selectedIds].map(id => {
                const p = products.find(pp => pp.id === id)
                return p?.search_filter_id || `pid:${id}`
              })
            )]
            const ok = await showConfirm(`선택된 상품이 속한 ${fmt(groupIds.length)}개 그룹의 AI 태그를 전체 삭제하시겠습니까?`)
            if (!ok) return
            try {
              const res = await proxyApi.clearAiTags(groupIds)
              if (res.success) {
                showAlert(res.message, 'success')
                const gidSet = new Set(groupIds)
                setAllProducts(prev => prev.map(p =>
                  (p.search_filter_id && gidSet.has(p.search_filter_id)) || gidSet.has(`pid:${p.id}`)
                    ? { ...p, tags: [], seo_keywords: [] }
                    : p
                ))
                setSelectedIds(new Set()); setSelectAll(false)
              } else showAlert(res.message, 'error')
            } catch (e) {
              showAlert(`태그 삭제 실패: ${e instanceof Error ? e.message : '알 수 없는 오류'}`, 'error')
            }
          }} style={{
            fontSize: "0.78rem", padding: "4px 12px",
            border: "1px solid rgba(255,107,107,0.4)", borderRadius: "5px",
            color: "#FF6B6B", background: "rgba(255,107,107,0.1)", cursor: "pointer", whiteSpace: "nowrap",
          }}>태그삭제</button>
          <button
            onClick={() => {
              if (selectedIds.size === 0) { showAlert('전송할 상품을 선택해주세요'); return }
              const ids = Array.from(selectedIds).join(',')
              const sites = [...new Set(
                Array.from(selectedIds).map(id => products.find(p => p.id === id)?.source_site).filter(Boolean)
              )].join(',')
              sessionStorage.setItem('shipment_selected', ids)
              sessionStorage.setItem('shipment_sites', sites)
              window.open('/samba/shipments?fromStorage=1&autoAll=1', '_blank')
            }}
            style={{
              fontSize: "0.78rem", padding: "4px 12px",
              border: "1px solid #3D3D3D", borderRadius: "5px",
              color: "#B0B0B0", background: "rgba(50,50,50,0.6)", cursor: "pointer", whiteSpace: "nowrap",
            }}>상품전송</button>
          <button
            onClick={handleBulkDelete}
            title="DB에서 정보 삭제"
            style={{
              fontSize: "0.78rem", padding: "4px 12px",
              border: "1px solid #3D3D3D", borderRadius: "5px",
              color: "#B0B0B0", background: "rgba(50,50,50,0.6)", cursor: "pointer", whiteSpace: "nowrap",
            }}
          >상품삭제</button>
          <button
            onClick={async () => {
              if (selectedIds.size === 0) { showAlert('상품을 선택해주세요'); return }
              // 전체선택 시 현재 페이지에 없는 상품도 서버에서 조회
              let marketPool: SambaCollectedProduct[] = allProducts.filter(p => selectedIds.has(p.id))
              if (marketPool.length < selectedIds.size) {
                try {
                  marketPool = await fetchProductsByIds([...selectedIds])
                } catch { /* 폴백: 현재 페이지만 */ }
              }
              const lockedMkt = marketPool.filter(p => p.lock_delete && (p.registered_accounts?.length ?? 0) > 0)
              const targets = marketPool.filter(p => !p.lock_delete && (p.registered_accounts?.length ?? 0) > 0)
              if (lockedMkt.length) showAlert(`삭제잠금 ${fmt(lockedMkt.length)}개 제외됩니다.`, 'info')
              if (!targets.length) { showAlert('마켓에 등록된 상품이 없습니다.'); return }
              openMarketDeleteModal(targets, 'bulk')
              return
              if (!await showConfirm(`${fmt(targets.length)}개 상품을 마켓에서 삭제(판매중지)하시겠습니까?`)) return
              aiJobAbortRef.current = false
              setAiJobTitle(`마켓삭제 (${fmt(targets.length)}건)`)
              setAiJobLogs([])
              setAiJobDone(false)
              setAiJobModal(true)
              let totalOk = 0, totalFail = 0
              // 로그를 배열 ref로 관리 — spread 복사 O(n²) 방지
              const logsRef: string[] = []
              const flushLogs = () => setAiJobLogs([...logsRef])
              // 성공 계정 누적 (루프 끝나고 한번에 상품 상태 갱신)
              const successMap = new Map<string, string[]>()
              const ts = fmtTime
              for (let i = 0; i < targets.length; i++) {
                if (aiJobAbortRef.current) { logsRef.push(`\n⛔ 사용자 중단 (${fmt(i)}/${fmt(targets.length)})`); flushLogs(); break }
                const t = targets[i]
                const name = t.name.slice(0, 20)
                try {
                  const accIds = t.registered_accounts ?? []
                  const res = await shipmentApi.marketDelete([t.id], accIds)
                  const result = res?.results?.[0]
                  if (result?.delete_results) {
                    const entries = Object.entries(result.delete_results as Record<string, string>)
                    const successAccIds: string[] = []
                    for (const [accId, status] of entries) {
                      const acc = accountsMap.get(accId)
                      const label = acc?.market_type || accId.slice(0, 8)
                      const isOk = status === 'success' || status.includes('성공')
                      if (isOk) { totalOk++; successAccIds.push(accId) } else totalFail++
                      logsRef.push(`[${ts()}] [${fmt(i + 1)}/${fmt(targets.length)}] ${name} → ${label}: ${isOk ? '✓' : '✗'}`)
                    }
                    if (successAccIds.length) successMap.set(t.id, successAccIds)
                  } else {
                    totalOk++
                    logsRef.push(`[${ts()}] [${fmt(i + 1)}/${fmt(targets.length)}] ${name} → ✓`)
                  }
                } catch {
                  totalFail++
                  logsRef.push(`[${ts()}] [${fmt(i + 1)}/${fmt(targets.length)}] ${name} → ✗`)
                }
                flushLogs()
                await new Promise(r => setTimeout(r, 50))
              }
              // 상품 상태 한번에 갱신
              if (successMap.size > 0) {
                setAllProducts(prev => prev.map(pp => {
                  const removedAccs = successMap.get(pp.id)
                  if (!removedAccs) return pp
                  const remaining = (pp.registered_accounts ?? []).filter(id => !removedAccs.includes(id))
                  return { ...pp, registered_accounts: remaining, status: remaining.length === 0 ? 'collected' : pp.status } as SambaCollectedProduct
                }))
              }
              logsRef.push(``, `성공 ${fmt(totalOk)} / 실패 ${fmt(totalFail)}`)
              flushLogs()
              setAiJobDone(true)
            }}
            title="등록마켓에서 상품 삭제"
            style={{
            fontSize: "0.78rem", padding: "4px 12px",
            border: "1px solid #3D3D3D", borderRadius: "5px",
            color: "#B0B0B0", background: "rgba(50,50,50,0.6)", cursor: "pointer", whiteSpace: "nowrap",
          }}>마켓삭제</button>
          <button
            onClick={async () => {
              if (selectedIds.size === 0) { showAlert('상품을 선택해주세요'); return }
              // 전체선택 시 현재 페이지에 없는 상품도 서버에서 조회
              let pool: SambaCollectedProduct[] = allProducts.filter(p => selectedIds.has(p.id))
              if (pool.length < selectedIds.size) {
                try {
                  pool = await fetchProductsByIds([...selectedIds])
                } catch { /* 폴백: 현재 페이지만 */ }
              }
              const targets = pool.filter(p => (p.registered_accounts?.length ?? 0) > 0)
              if (!targets.length) { showAlert('마켓에 등록된 상품이 없습니다.'); return }
              openMarketDeleteModal(targets, 'bulk', 'force')
            }}
            title="판매마켓에서 직접 삭제 후 연결 끊긴 상품 판매처 기록 삭제"
            style={{
              fontSize: "0.78rem", padding: "4px 12px",
              border: "1px solid #3D3D3D", borderRadius: "5px",
              color: "#B0B0B0", background: "rgba(50,50,50,0.6)", cursor: "pointer", whiteSpace: "nowrap",
            }}
          >강제삭제</button>
          <button
            onClick={() => setGhostChoiceModal(true)}
            title="마켓에는 등록되어 있지만 DB 매핑이 끊어진 유령 상품 정리 (스스/11번가 선택)"
            style={{
              fontSize: "0.78rem", padding: "4px 12px",
              border: "1px solid #3D3D3D", borderRadius: "5px",
              color: "#B0B0B0", background: "rgba(50,50,50,0.6)", cursor: "pointer", whiteSpace: "nowrap",
            }}
          >유령삭제</button>
          <button
            onClick={async () => {
              if (selectedIds.size === 0) { showAlert('상품을 선택해주세요'); return }
              const ids = Array.from(selectedIds)
              setRefreshDetails([])
              setRefreshModal(true)
              setRefreshLoading(true)
              try {
                const res = await collectorApi.refresh(ids)
                setRefreshDetails(res.details ?? [])
                setRefreshSummary(`${fmt(res.total)}건 중 ${fmt(res.changed)}건 변동, ${fmt(res.sold_out)}건 품절${res.retransmitted ? `, ${fmt(res.retransmitted)}건 재전송` : ''}, ${fmt(res.errors)}건 에러`)
              } catch {
                setRefreshSummary('갱신 실패')
              }
              setRefreshLoading(false)
              reloadProducts()
            }}
            style={{
              fontSize: "0.78rem", padding: "4px 12px",
              border: "1px solid #3D3D3D", borderRadius: "5px",
              color: "#B0B0B0", background: "rgba(50,50,50,0.6)", cursor: "pointer", whiteSpace: "nowrap",
            }}
          >업데이트</button>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
          <button
            onClick={() => { setViewMode("compact"); setExpandedIds(new Set()) }}
            style={{
              fontSize: "0.75rem", padding: "0.25rem 0.75rem", borderRadius: "6px", cursor: "pointer",
              border: viewMode === "compact" ? "1px solid #FF8C00" : "1px solid #3D3D3D",
              color: viewMode === "compact" ? "#FF8C00" : "#C5C5C5",
              background: viewMode === "compact" ? "rgba(255,140,0,0.15)" : "transparent",
            }}
          >간단</button>
          <button
            onClick={() => setViewMode("card")}
            style={{
              fontSize: "0.75rem", padding: "0.25rem 0.75rem", borderRadius: "6px", cursor: "pointer",
              border: viewMode === "card" ? "1px solid #FF8C00" : "1px solid #3D3D3D",
              color: viewMode === "card" ? "#FF8C00" : "#C5C5C5",
              background: viewMode === "card" ? "rgba(255,140,0,0.15)" : "transparent",
            }}
          >자세히</button>
          <button
            onClick={() => setViewMode("image")}
            style={{
              fontSize: "0.75rem", padding: "0.25rem 0.75rem", borderRadius: "6px", cursor: "pointer",
              border: viewMode === "image" ? "1px solid #FF8C00" : "1px solid #3D3D3D",
              color: viewMode === "image" ? "#FF8C00" : "#C5C5C5",
              background: viewMode === "image" ? "rgba(255,140,0,0.15)" : "transparent",
            }}
          >사진</button>
          <select
            value={aiFilter}
            onChange={(e) => setAiFilter(e.target.value)}
            style={{ background: '#1A1A1A', border: '1px solid #3D3D3D', color: '#E5E5E5', borderRadius: '6px', padding: '0.25rem 0.5rem', fontSize: '0.75rem' }}
          >
            <option value="">전체</option>
            <option value="ai_tag_yes">AI태그 적용</option>
            <option value="ai_tag_no">AI태그 미적용</option>
            <option value="ai_img_yes">AI이미지 적용</option>
            <option value="ai_img_no">AI이미지 미적용</option>
            <option value="filter_yes">필터링완료</option>
            <option value="filter_no">필터링미완료</option>
            <option value="img_edit_yes">이미지수정완료</option>
            <option value="img_edit_no">이미지수정미완료</option>
            <option value="video_yes">영상있음</option>
            <option value="video_no">영상없음</option>
            <option value="has_orders">판매이력상품</option>
          </select>
          <select
            value={sortBy}
            onChange={(e) => setSortBy(e.target.value)}
            style={{
              width: "auto", padding: "0.25rem 0.5rem", fontSize: "0.75rem",
              background: "#1A1A1A", border: "1px solid #3D3D3D", color: "#C5C5C5", borderRadius: "6px",
            }}
          >
            <option value="collect-desc">수집일 최신순</option>
            <option value="collect-asc">수집일 오래된순</option>
            <option value="update-desc">업데이트일 최신순</option>
            <option value="update-asc">업데이트일 오래된순</option>
          </select>
          <select value={pageSize} onChange={e => { setPageSize(Number(e.target.value)); setCurrentPage(1) }}
            style={{ padding: '0.25rem 0.5rem', fontSize: '0.75rem', background: '#1A1A1A', border: '1px solid #3D3D3D', color: '#C5C5C5', borderRadius: '6px' }}>
            <option value={20}>20건</option>
            <option value={50}>50건</option>
            <option value={100}>100건</option>
          </select>
        </div>
      </div>

      {/* Product list */}
      {loading && products.length === 0 ? (
        /* 스켈레톤 — 빈 화면 대신 카드 형태 placeholder (체감 속도 향상) */
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: viewMode === 'compact' ? '4px' : '8px' }}>
          {Array.from({ length: Math.min(pageSize, 10) }).map((_, i) => (
            <div
              key={i}
              style={{
                minWidth: 0,
                height: viewMode === 'compact' ? '180px' : '240px',
                background: 'linear-gradient(90deg, #1A1A1A 0%, #232323 50%, #1A1A1A 100%)',
                backgroundSize: '200% 100%',
                borderRadius: '8px',
                border: '1px solid #2D2D2D',
                animation: 'sambaSkeletonPulse 1.4s ease-in-out infinite',
              }}
            />
          ))}
          <style jsx>{`
            @keyframes sambaSkeletonPulse {
              0% { background-position: 200% 0; }
              100% { background-position: -200% 0; }
            }
          `}</style>
        </div>
      ) : loading ? (
        <div style={{ padding: "3rem", textAlign: "center", color: "#555", fontSize: "0.9rem" }}>로딩 중...</div>
      ) : loadError ? (
        <div style={{ padding: "3rem", textAlign: "center", fontSize: "0.85rem" }}>
          <div style={{ color: "#FF6B6B", marginBottom: "8px" }}>서버 연결에 실패했습니다</div>
          <button
            onClick={() => loadProducts()}
            style={{ padding: "6px 16px", borderRadius: "6px", fontSize: "0.8rem", background: "rgba(255,107,107,0.15)", border: "1px solid rgba(255,107,107,0.4)", color: "#FF6B6B", cursor: "pointer" }}
          >다시 시도</button>
        </div>
      ) : products.length === 0 ? (
        <div style={{ padding: "3rem", textAlign: "center", color: "#555", fontSize: "0.9rem" }}>
          등록된 상품이 없습니다
        </div>
      ) : viewMode === "image" ? (
        /* Image grid view */
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))", gap: "8px" }}>
          {products.map((p) => (
            <div key={p.id} style={{
              background: "rgba(30,30,30,0.5)",
              border: selectedIds.has(p.id) ? "1px solid #FF8C00" : "1px solid #2D2D2D",
              borderRadius: "8px",
              overflow: "hidden", cursor: "pointer", position: "relative",
            }} onClick={() => handleCheckboxToggle(p.id, !selectedIds.has(p.id))}>
              <input
                type="checkbox"
                checked={selectedIds.has(p.id)}
                onChange={e => handleCheckboxToggle(p.id, e.target.checked)}
                onClick={e => e.stopPropagation()}
                style={{
                  position: "absolute", top: "6px", left: "6px", zIndex: 1,
                  accentColor: "#FF8C00", width: "14px", height: "14px", cursor: "pointer",
                }}
              />
              <div onClick={(e) => { e.stopPropagation(); router.push(`/samba/products?search_type=id&search=${p.id}&highlight=${p.id}`); }} style={{ cursor: 'pointer' }}>
                <ProductImage src={p.images?.[0]} name={p.name} size={140} />
              </div>
              {(p.free_shipping || p.same_day_delivery) && (
                <div style={{ display: 'flex', gap: '3px', padding: '3px 8px 0' }}>
                  {p.free_shipping && <span style={{ fontSize: '0.6rem', padding: '1px 5px', borderRadius: '3px', background: 'rgba(76,154,255,0.15)', color: '#4C9AFF', fontWeight: 600 }}>무배</span>}
                  {p.same_day_delivery && <span style={{ fontSize: '0.6rem', padding: '1px 5px', borderRadius: '3px', background: 'rgba(255,140,0,0.15)', color: '#FF8C00', fontWeight: 600 }}>당발</span>}
                </div>
              )}
              <div style={{ padding: "6px 8px" }}>
                <p style={{ fontSize: '0.7rem', color: '#C5C5C5', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', margin: 0, display: 'flex', alignItems: 'center' }}>
                  <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{p.name}</span>
                </p>
                <p style={{ fontSize: "0.75rem", color: "#FF8C00", fontWeight: 600, margin: 0 }}>₩{fmt(p.sale_price)}</p>
              </div>
            </div>
          ))}
        </div>
      ) : (
        /* Card / Compact view — 2열 그리드 */
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: viewMode === 'compact' ? '4px' : '8px' }}>
          {products.map((p, idx) => (
            <div key={p.id} style={{ minWidth: 0 }}>
              <ProductCard
                product={p}
                idx={idx}
                compact={viewMode === 'compact'}
                expanded={expandedIds.has(p.id)}
                onToggleExpand={() => handleToggleExpand(p.id)}
                policies={policies}
                accounts={accounts}
                nameRules={nameRules}
                selectedIds={selectedIds}
                filterNameMap={filterNameMap}
                deletionWords={deletionWords}
                onCheckboxToggle={handleCheckboxToggle}
                onDelete={handleDelete}
                onPolicyChange={handlePolicyChange}
                onToggleMarket={handleToggleMarket}
                onEnrich={handleEnrich}
                onLockToggle={handleLockToggle}
                onBlockCollect={handleBlockCollect}
                onMarketDelete={handleMarketDelete}
                onProductUpdate={handleProductUpdate}
                onTagUpdate={handleTagUpdate}
                logMessage={activeLog?.productId === p.id ? activeLog.message : undefined}
                catMappingMap={catMappingMap}
                filters={searchFilters}
                detailTemplates={detailTemplates}
              />
            </div>
          ))}
        </div>
      )}

      {/* 페이지네이션 */}
      {serverTotal > 0 && (
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '0.25rem', padding: '1rem 0', flexWrap: 'wrap' }}>
          <button onClick={() => goToPage(1)} disabled={currentPage === 1}
            style={{ padding: '4px 8px', fontSize: '0.75rem', border: '1px solid #2D2D2D', borderRadius: '4px', background: 'transparent', color: currentPage === 1 ? '#444' : '#C5C5C5', cursor: currentPage === 1 ? 'default' : 'pointer' }}>{'<<'}</button>
          <button onClick={() => goToPage(currentPage - 1)} disabled={currentPage === 1}
            style={{ padding: '4px 8px', fontSize: '0.75rem', border: '1px solid #2D2D2D', borderRadius: '4px', background: 'transparent', color: currentPage === 1 ? '#444' : '#C5C5C5', cursor: currentPage === 1 ? 'default' : 'pointer' }}>{'<'}</button>
          {(() => {
            const pages: number[] = []
            const start = Math.max(1, currentPage - 4)
            const end = Math.min(totalPages, start + 9)
            for (let i = start; i <= end; i++) pages.push(i)
            return pages.map(p => (
              <button key={p} onClick={() => goToPage(p)}
                style={{ padding: '4px 10px', fontSize: '0.75rem', border: p === currentPage ? '1px solid #FF8C00' : '1px solid #2D2D2D', borderRadius: '4px', background: p === currentPage ? 'rgba(255,140,0,0.15)' : 'transparent', color: p === currentPage ? '#FF8C00' : '#C5C5C5', cursor: 'pointer', fontWeight: p === currentPage ? 700 : 400 }}>{p}</button>
            ))
          })()}
          <button onClick={() => goToPage(currentPage + 1)} disabled={currentPage === totalPages}
            style={{ padding: '4px 8px', fontSize: '0.75rem', border: '1px solid #2D2D2D', borderRadius: '4px', background: 'transparent', color: currentPage === totalPages ? '#444' : '#C5C5C5', cursor: currentPage === totalPages ? 'default' : 'pointer' }}>{'>'}</button>
          <button onClick={() => goToPage(totalPages)} disabled={currentPage === totalPages}
            style={{ padding: '4px 8px', fontSize: '0.75rem', border: '1px solid #2D2D2D', borderRadius: '4px', background: 'transparent', color: currentPage === totalPages ? '#444' : '#C5C5C5', cursor: currentPage === totalPages ? 'default' : 'pointer' }}>{'>>'}</button>
          <span style={{ fontSize: '0.75rem', color: '#888', marginLeft: '0.5rem' }}>
            {fmt(serverTotal)}건 / {currentPage}/{fmt(totalPages)}p
          </span>
        </div>
      )}
    </div>
  );
}

