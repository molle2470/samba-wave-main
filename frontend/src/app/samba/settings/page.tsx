'use client'

import { useEffect } from 'react'
import { useProxySettings } from './hooks/useProxySettings'
import { ProxySettingsPanel } from './components/ProxySettingsPanel'
import { useExternalSettings } from './hooks/useExternalSettings'
import { ExternalIntegrationsPanel } from './components/ExternalIntegrationsPanel'
import { useStoreSettings } from './hooks/useStoreSettings'
import { StoreSettingsPanel } from './components/StoreSettingsPanel'
import { useSourcingAccounts } from './hooks/useSourcingAccounts'
import { SourcingAccountsPanel } from './components/SourcingAccountsPanel'
import { LicensePanel } from './components/LicensePanel'

export default function SettingsPage() {
  useEffect(() => { document.title = 'SAMBA-설정' }, [])

  // 훅
  const proxySettings = useProxySettings()
  const externalSettings = useExternalSettings()
  const storeSettings = useStoreSettings()
  const sourcingAccountsHook = useSourcingAccounts()
  const { loadSourcingAccounts } = sourcingAccountsHook

  const { loadAccounts, loadStoreSettings } = storeSettings
  const { loadExchangeRates, loadExternalSettings, loadProbeStatus } = externalSettings
  const { loadProxies } = proxySettings

  useEffect(() => {
    loadAccounts()
    loadSourcingAccounts()
    loadProxies()
  }, [loadAccounts, loadSourcingAccounts, loadProxies])

  useEffect(() => {
    loadExchangeRates()
    loadExternalSettings()
    loadProbeStatus()
    loadStoreSettings()
  }, [loadExchangeRates, loadExternalSettings, loadProbeStatus, loadStoreSettings])

  return (
    <div style={{ color: '#E5E5E5' }}>
      <StoreSettingsPanel {...storeSettings} />

      {/* 소싱처 계정 관리 */}
      <SourcingAccountsPanel {...sourcingAccountsHook} />

      <ExternalIntegrationsPanel
        {...externalSettings}
        visiblePasswords={storeSettings.visiblePasswords}
        togglePasswordVisibility={storeSettings.togglePasswordVisibility}
      />

      {/* 프록시 설정 */}
      <ProxySettingsPanel {...proxySettings} />

      {/* 라이선스 */}
      <LicensePanel />
    </div>
  )
}
