'use client'

import {
  collectorApi,
  type SambaOrder,
  type SambaMarketAccount,
} from '@/lib/samba/api/commerce'
import { showAlert } from '@/components/samba/Modal'

export function useOrderLinks(accounts: SambaMarketAccount[]) {
  const handleSourceLink = async (o: SambaOrder) => {
    // 1. source_url 우선 (구 LotteON productDetail.lotte 형식은 무효 — API 역추적으로 fallback)
    if (o.source_url && !o.source_url.includes('productDetail.lotte')) {
      window.open(o.source_url, '_blank')
      return
    }
    // 2. product_id가 URL이면 직접 열기
    if (o.product_id && o.product_id.startsWith('http')) {
      window.open(o.product_id, '_blank')
      return
    }
    // 3. 마켓 상품번호로 수집상품 역추적
    if (o.product_id) {
      try {
        const res = await collectorApi.lookupByMarketNo(o.product_id)
        if (res.found && res.original_link) {
          window.open(res.original_link, '_blank')
          return
        }
      } catch { /* ignore */ }
    }
    // 4. fallback 금지 — 상품명 끝 숫자(`\d{6,}`)를 상품번호로 가정하면 옵션/스타일코드와
    // 충돌해 엉뚱한 상품(예: 스파이더 ↔ 푸마) 페이지가 열리는 사고가 반복됨(2026-05-20).
    // 백엔드 주문동기화에서 source_url을 채우지 못한 케이스는 안전하게 안내만 한다.
    showAlert('소싱처 원문링크 정보가 없습니다', 'info')
  }

  const handleMarketLink = async (o: SambaOrder) => {
    const acc = accounts.find(a => a.id === o.channel_id)
    const marketType = acc?.market_type || ''
    const sellerId = acc?.seller_id || ''
    const storeSlug = (acc?.additional_fields as Record<string, string> | undefined)?.storeSlug || ''
    let productNo = o.product_id || ''

    // 스마트스토어 정상 URL은 channelProductNo로 열림 (origin은 "존재하지 않는 페이지").
    // - mpn[acc.id]        = channelProductNo (URL용)
    // - mpn[acc.id_origin] = originProductNo  (Commerce API 관리/품절용)
    // 주문 product_id에는 channelProductNo가 들어오지만, 선물하기/옵션상품 등은
    // originalProductId(=originProductNo) fallback이 저장되어 그대로 쓰면 깨짐
    // → 수집상품 역추적으로 channelProductNo 우선 해석 (ProductCard.tsx:539와 동일 패턴)
    if (marketType === 'smartstore' && o.product_id && o.channel_id) {
      try {
        const lookup = await collectorApi.lookupByMarketNo(o.product_id)
        const mpn = lookup?.market_product_nos || {}
        // dict 형태({originProductNo, smartstoreChannelProductNo, groupProductNo})
        // URL용으로는 smartstoreChannelProductNo > groupProductNo 우선
        const pickForUrl = (v: unknown): string => {
          if (v && typeof v === 'object') {
            const obj = v as Record<string, unknown>
            return String(
              obj.smartstoreChannelProductNo ??
              obj.groupProductNo ??
              obj.originProductNo ??
              ''
            )
          }
          return v ? String(v) : ''
        }
        const resolved =
          pickForUrl(mpn[o.channel_id]) ||
          pickForUrl(mpn[`${o.channel_id}_origin`])
        if (resolved) productNo = resolved
      } catch { /* lookup 실패 시 product_id 그대로 사용 */ }
    }

    const urlMap: Record<string, string> = {
      smartstore: `https://smartstore.naver.com/${storeSlug || sellerId}/products/${productNo}`,
      coupang: `https://www.coupang.com/vp/products/${productNo}`,
      '11st': `https://www.11st.co.kr/products/${productNo}`,
      gmarket: `https://item.gmarket.co.kr/Item?goodscode=${productNo}`,
      auction: `https://itempage3.auction.co.kr/DetailView.aspx?ItemNo=${productNo}`,
      ssg: `https://www.ssg.com/item/itemView.ssg?itemId=${productNo}`,
      lotteon: `https://www.lotteon.com/p/product/${productNo}`,
      lottehome: `https://www.lotteimall.com/goods/viewGoodsDetail.lotte?goods_no=${productNo}`,
      kream: `https://kream.co.kr/products/${productNo}`,
      ebay: `https://www.ebay.com/itm/${productNo}`,
    }

    if (productNo) {
      // PlayAuto 판매처 별칭: 신규 sales_channel_alias 우선, 없으면 레거시 source_site
      const playautoAlias = (o.sales_channel_alias || o.source_site || '').trim()
      if (marketType === 'playauto' && playautoAlias) {
        const site = playautoAlias.split('(')[0]
        const siteUrlMap: Record<string, (no: string) => string> = {
          'GS이숍': (no) => `https://www.gsshop.com/prd/prd.gs?prdid=${no}`,
          'G마켓': (no) => `https://item.gmarket.co.kr/Item?goodscode=${no}`,
          '옥션': (no) => `https://itempage3.auction.co.kr/DetailView.aspx?ItemNo=${no}`,
          '11번가': (no) => `https://www.11st.co.kr/products/${no}`,
          '스마트스토어': (no) => `https://smartstore.naver.com/search?q=${encodeURIComponent(no)}`,
          '쿠팡': (no) => `https://www.coupang.com/vp/products/${no}`,
          'SSG': (no) => `https://www.ssg.com/item/itemView.ssg?itemId=${no}`,
          '롯데ON': (no) => `https://www.lotteon.com/p/product/${no}`,
          '롯데온': (no) => `https://www.lotteon.com/p/product/${no}`,
          '롯데홈쇼핑': (no) => `https://www.lotteimall.com/goods/viewGoodsDetail.lotte?goods_no=${no}`,
          '롯데아이몰': (no) => `https://www.lotteimall.com/goods/viewGoodsDetail.lotte?goods_no=${no}`,
          '홈앤쇼핑': (no) => `https://www.hmall.com/p/pda/itemPtc.do?slitmCd=${no}`,
          'HMALL': (no) => `https://www.hmall.com/p/pda/itemPtc.do?slitmCd=${no}`,
        }
        const builder = siteUrlMap[site]
        if (builder) {
          const cleanNo = site === 'GS이숍' && productNo.length > 3
            ? productNo.slice(0, -3)
            : productNo
          window.open(builder(cleanNo), '_blank'); return
        }
      }

      const url = urlMap[marketType]
      if (url) { window.open(url, '_blank'); return }
    }

    const searchMap: Record<string, string> = {
      smartstore: `https://search.shopping.naver.com/search/all?query=`,
      coupang: `https://www.coupang.com/np/search?q=`,
      '11st': `https://search.11st.co.kr/Search.tmall?kwd=`,
      ssg: `https://www.ssg.com/search.ssg?query=`,
    }
    const searchBase = searchMap[marketType]
    if (searchBase) {
      window.open(searchBase + encodeURIComponent(o.product_name || ''), '_blank')
    } else if (o.product_name) {
      window.open(`https://search.shopping.naver.com/search/all?query=${encodeURIComponent(o.product_name)}`, '_blank')
    } else {
      showAlert('판매마켓 링크를 생성할 수 없습니다', 'error')
    }
  }

  return { handleSourceLink, handleMarketLink }
}
