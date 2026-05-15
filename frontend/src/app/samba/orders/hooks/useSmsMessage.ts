'use client'

import { useState, useRef, useCallback } from 'react'
import {
  proxyApi,
  type SambaOrder,
  type SambaMarketAccount,
  type MessageLog,
} from '@/lib/samba/api/commerce'
import { showAlert } from '@/components/samba/Modal'

interface SmsTemplate {
  id: string
  label: string
  msg: string
}

const DEFAULT_SMS_TEMPLATES: SmsTemplate[] = [
  { id: 't1', label: '주문취소안내', msg: '{{marketName}} 주문취소안내\n주문상품 : {{goodsName}}\n\n안녕하세요, {{rvcName}} 고객님.\n\n해당 상품이 일시적으로 시스템 오류로 노출되어 주문이 접수된 것으로 확인되었습니다.\n\n불편을 드려 정말 죄송합니다.\n\n빠른 환불 처리를 위해 "단순취소" 사유로 주문취소 해주시면 확인 후 바로 환불도와드리겠습니다.\n\n불편을 드려 진심으로 죄송하며, 더 나은 서비스로 보답드리겠습니다. 감사합니다.' },
  { id: 't2', label: '가격변동 취소', msg: '{{marketName}} 가격변동 안내\n주문상품 : {{goodsName}}\n\n안녕하세요 {{rvcName}} 고객님\n\n해당 제품 공급처에서 가격을 변동하여 안내드립니다.\n취소 후 재주문 부탁드립니다.' },
  { id: 't3', label: '국내상품 발주안내', msg: '{{marketName}} 주문상품 발주 완료\n주문상품 : {{goodsName}}\n\n안녕하세요 {{rvcName}} 고객님^^ 발주 완료되었습니다. 배송완료까지 영업일기준 2~3일정도 소요됩니다.' },
  { id: 't4', label: '반품비', msg: '{{marketName}} 반품비 안내\n상품명 : {{goodsName}}\n\n반품비 안내드립니다.\n교환비용 8,000원 발생(고객변심)하므로 따로 개별 문자 안내드리겠습니다.' },
  { id: 't5', label: '반품안내문자', msg: '{{marketName}} 반품 안내\n주문상품 : {{goodsName}}\n\n안녕하세요 {{rvcName}} 고객님\n반품신청으로 문자안내드립니다.\n교환 접수시 회수기사님 방문2-3일내 이루어지며 회수된 상품 해당부서로 이동하여 검수진행과정 진행됩니다.' },
  { id: 't6', label: '발주 후 품절', msg: '{{marketName}} 품절안내\n주문상품 : {{goodsName}}\n\n안녕하세요 {{rvcName}} 고객님. 저희가 해당 제품 발주를 넣었는데 공급처에서 품절이라고 연락이 왔습니다.\n취소 처리 도와드리겠습니다.' },
]

export function useSmsMessage(accounts: SambaMarketAccount[]) {
  const [msgModal, setMsgModal] = useState<{ type: 'sms' | 'kakao'; order: SambaOrder } | null>(null)
  const [msgText, setMsgText] = useState('')
  const [msgSending, setMsgSending] = useState(false)
  const msgTextRef = useRef<HTMLTextAreaElement>(null)
  const [msgHistory, setMsgHistory] = useState<MessageLog[]>([])
  const [sentFlags, setSentFlags] = useState<Record<string, { sms: boolean; kakao: boolean }>>({})

  // SMS 템플릿 (localStorage 저장)
  const [smsTemplates, setSmsTemplates] = useState<SmsTemplate[]>(() => {
    try {
      const saved = typeof window !== 'undefined' ? localStorage.getItem('samba_sms_templates') : null
      return saved ? JSON.parse(saved) : DEFAULT_SMS_TEMPLATES
    } catch { return DEFAULT_SMS_TEMPLATES }
  })
  const [templateEditModal, setTemplateEditModal] = useState<SmsTemplate | null>(null)
  const [isNewTemplate, setIsNewTemplate] = useState(false)

  const saveSmsTemplates = (templates: SmsTemplate[]) => {
    setSmsTemplates(templates)
    localStorage.setItem('samba_sms_templates', JSON.stringify(templates))
  }
  const openNewTemplate = () => {
    setIsNewTemplate(true)
    setTemplateEditModal({ id: `t_${Date.now()}`, label: '', msg: '' })
  }
  const openEditTemplate = (t: SmsTemplate) => {
    setIsNewTemplate(false)
    setTemplateEditModal({ ...t })
  }
  const saveTemplate = () => {
    if (!templateEditModal) return
    if (isNewTemplate) {
      saveSmsTemplates([...smsTemplates, templateEditModal])
    } else {
      saveSmsTemplates(smsTemplates.map(t => t.id === templateEditModal.id ? templateEditModal : t))
    }
    setTemplateEditModal(null)
  }
  const deleteTemplate = (id: string) => {
    saveSmsTemplates(smsTemplates.filter(t => t.id !== id))
  }

  const insertMsgTag = (tag: string) => {
    const el = msgTextRef.current
    if (!el) { setMsgText(prev => prev + tag); return }
    const start = el.selectionStart
    const end = el.selectionEnd
    const newVal = msgText.slice(0, start) + tag + msgText.slice(end)
    setMsgText(newVal)
    requestAnimationFrame(() => { el.selectionStart = el.selectionEnd = start + tag.length; el.focus() })
  }

  const renderMsgTemplate = useCallback((template: string, order: SambaOrder) => {
    const matchedAccount = accounts.find(account => {
      if (order.channel_name && account.market_name && order.channel_name.includes(account.market_name)) return true
      if (order.channel_name && account.account_label && order.channel_name.includes(account.account_label)) return true
      return false
    })
    const sellerName = matchedAccount?.business_name || matchedAccount?.seller_id || matchedAccount?.account_label || ''
    let marketName = matchedAccount?.market_name || order.channel_name || ''
    // 플레이오토 주문은 실제 판매처가 sales_channel_alias(예: GSSHOP(고경))에 있음 → 그 별칭으로 치환
    if (marketName.includes('플레이오토')) {
      const alias = String(order.sales_channel_alias || '').trim()
      if (alias) marketName = alias
    }
    const variables: Record<string, string> = {
      sellerName,
      marketName,
      OrderName: order.order_number || '',
      rvcName: order.customer_name || '',
      rcvHPNo: order.customer_phone || '',
      goodsName: order.product_name || '',
    }

    return template
      .replace(/\{\{(sellerName|marketName|OrderName|rvcName|rcvHPNo|goodsName)\}\}/g, (_, key: keyof typeof variables) => variables[key] || '')
      .replace(/\{\{[^{}]+\}\}/g, '')
  }, [accounts])

  const openMsgModal = (type: 'sms' | 'kakao', order: SambaOrder) => {
    if (!order.customer_phone) {
      showAlert('고객 전화번호가 없습니다', 'error')
      return
    }
    setMsgModal({ type, order })
    setMsgText('')
    setMsgHistory([])
    proxyApi.fetchMessageHistory(order.id).then(setMsgHistory).catch(() => {})
  }

  const handleSendMsg = async () => {
    if (!msgModal || !msgText.trim()) {
      showAlert('메시지를 입력해주세요', 'error')
      return
    }
    setMsgSending(true)
    try {
      const phone = msgModal.order.customer_phone || ''
      const renderedMsg = renderMsgTemplate(msgText, msgModal.order).trim()
      if (!renderedMsg) {
        showAlert('치환 후 발송할 메시지가 비어 있습니다.', 'error')
        return
      }
      const orderId = msgModal.order.id
      const msgType = msgModal.type
      let res: { success: boolean; message: string }
      if (msgType === 'sms') {
        res = await proxyApi.sendSms(phone, renderedMsg, '', orderId, msgText)
      } else {
        res = await proxyApi.sendKakao(phone, renderedMsg, '', '', orderId, msgText)
      }
      if (res.success) {
        showAlert(res.message, 'success')
        setSentFlags(prev => ({
          ...prev,
          [orderId]: { ...prev[orderId], sms: msgType === 'sms' ? true : (prev[orderId]?.sms ?? false), kakao: msgType === 'kakao' ? true : (prev[orderId]?.kakao ?? false) },
        }))
        setMsgModal(null)
        setMsgText('')
      } else {
        showAlert(res.message, 'error')
      }
    } catch (e) {
      showAlert(e instanceof Error ? e.message : '발송 실패', 'error')
    } finally {
      setMsgSending(false)
    }
  }

  return {
    msgModal, setMsgModal,
    msgText, setMsgText,
    msgSending,
    msgTextRef,
    msgHistory,
    sentFlags, setSentFlags,
    smsTemplates,
    templateEditModal, setTemplateEditModal,
    isNewTemplate,
    openNewTemplate,
    openEditTemplate,
    saveTemplate,
    deleteTemplate,
    insertMsgTag,
    openMsgModal,
    handleSendMsg,
  }
}
