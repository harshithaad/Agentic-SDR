import { useState } from 'react'
import MetricsBar from './components/MetricsBar'
import LeadTable from './components/LeadTable'
import HumanReviewQueue from './components/HumanReviewQueue'
import UploadCSV from './components/UploadCSV'
import LeadDetail from './components/LeadDetail'
import SellerProfile from './components/SellerProfile'
import useLiveEvents from './hooks/useLiveEvents'

type Tab = 'dashboard' | 'leads' | 'review' | 'profile'

export default function App() {
  const [activeTab, setActiveTab] = useState<Tab>('dashboard')
  const [selectedLeadId, setSelectedLeadId] = useState<string | null>(null)
  const { tick, connected } = useLiveEvents()

  const tabs: { key: Tab; label: string }[] = [
    { key: 'dashboard', label: 'Dashboard' },
    { key: 'leads', label: 'Leads' },
    { key: 'review', label: 'Review Queue' },
    { key: 'profile', label: 'Profile' },
  ]

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100">
      {/* Header */}
      <header className="bg-gray-900 border-b border-gray-800 px-6 py-4 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-indigo-600 flex items-center justify-center text-white font-bold text-sm">
            AI
          </div>
          <h1 className="text-xl font-semibold text-white">Agentic SDR</h1>
          <span className="text-xs bg-indigo-900 text-indigo-300 px-2 py-0.5 rounded-full">
            Event-Driven
          </span>
          <span
            className={`flex items-center gap-1.5 text-xs px-2 py-0.5 rounded-full ${
              connected ? 'bg-green-900/60 text-green-300' : 'bg-gray-800 text-gray-500'
            }`}
            title={connected ? 'Live event stream connected' : 'Falling back to polling'}
          >
            <span
              className={`w-1.5 h-1.5 rounded-full ${
                connected ? 'bg-green-400 animate-pulse' : 'bg-gray-500'
              }`}
            />
            {connected ? 'Live' : 'Polling'}
          </span>
        </div>
        <nav className="flex gap-1">
          {tabs.map((t) => (
            <button
              key={t.key}
              onClick={() => setActiveTab(t.key)}
              className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                activeTab === t.key
                  ? 'bg-indigo-600 text-white'
                  : 'text-gray-400 hover:text-white hover:bg-gray-800'
              }`}
            >
              {t.label}
            </button>
          ))}
        </nav>
      </header>

      {/* Metrics Bar */}
      <MetricsBar eventTick={tick} />

      {/* Main content */}
      <main className="px-6 py-6">
        {activeTab === 'dashboard' && (
          <div className="space-y-6">
            <UploadCSV />
            <LeadTable onSelectLead={setSelectedLeadId} eventTick={tick} />
          </div>
        )}
        {activeTab === 'leads' && (
          <LeadTable onSelectLead={setSelectedLeadId} eventTick={tick} />
        )}
        {activeTab === 'review' && (
          <HumanReviewQueue onSelectLead={setSelectedLeadId} eventTick={tick} />
        )}
        {activeTab === 'profile' && <SellerProfile />}
      </main>

      {/* Lead Detail Side Panel */}
      {selectedLeadId && (
        <LeadDetail
          leadId={selectedLeadId}
          onClose={() => setSelectedLeadId(null)}
        />
      )}
    </div>
  )
}
