import { useEffect, useState } from 'react'
import axios from 'axios'

interface Lead {
  id: string
  company_name: string
  contact_name?: string
  contact_email?: string
  review_reason?: string
  intent_reasoning?: string
  intent_confidence?: number
  human_approval_required?: boolean
  error_message?: string
  updated_at?: string
}

interface Props {
  onSelectLead: (id: string) => void
  eventTick?: number
}

export default function HumanReviewQueue({ onSelectLead, eventTick = 0 }: Props) {
  const [leads, setLeads] = useState<Lead[]>([])
  const [loading, setLoading] = useState(true)

  const fetchLeads = async () => {
    try {
      const res = await axios.get('/api/leads', { params: { status: 'HUMAN_REVIEW' } })
      setLeads(res.data.leads)
    } catch {
      // ignore
    } finally {
      setLoading(false)
    }
  }

  // SSE events drive refresh; the interval is only a fallback
  useEffect(() => {
    fetchLeads()
  }, [eventTick])

  useEffect(() => {
    const interval = setInterval(fetchLeads, 30000)
    return () => clearInterval(interval)
  }, [])

  const handleAction = async (leadId: string, action: string) => {
    try {
      await axios.patch(`/api/leads/${leadId}/human-action`, { action })
      await fetchLeads()
    } catch (e: any) {
      alert(e?.response?.data?.detail || 'Action failed')
    }
  }

  if (loading) {
    return (
      <div className="text-center py-16 text-gray-500">Loading review queue...</div>
    )
  }

  if (leads.length === 0) {
    return (
      <div className="text-center py-16">
        <div className="text-4xl mb-4">✓</div>
        <p className="text-gray-400 text-lg font-medium">All clear!</p>
        <p className="text-gray-600 text-sm mt-1">No leads require human review.</p>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between mb-2">
        <h2 className="text-lg font-semibold text-white">Review Queue</h2>
        <span className="bg-orange-900 text-orange-300 text-xs font-bold px-3 py-1 rounded-full">
          {leads.length} pending
        </span>
      </div>

      {leads.map((lead) => (
        <div
          key={lead.id}
          className="bg-gray-900 border border-orange-900/50 rounded-xl p-5"
        >
          <div className="flex items-start justify-between gap-4">
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 mb-1">
                <span className="inline-block w-2 h-2 rounded-full bg-orange-500 flex-shrink-0" />
                <h3 className="font-semibold text-white">{lead.company_name}</h3>
              </div>
              {lead.contact_name && (
                <p className="text-sm text-gray-400 mb-2">
                  {lead.contact_name}
                  {lead.contact_email ? ` — ${lead.contact_email}` : ''}
                </p>
              )}

              {/* Escalation reason — from the backend, not hardcoded */}
              <div className="bg-gray-800 rounded-lg p-3 text-sm space-y-2 mt-3">
                {lead.review_reason && (
                  <p className="text-orange-300">
                    <span className="font-semibold">Reason:</span> {lead.review_reason}
                  </p>
                )}
                {lead.intent_reasoning && (
                  <p className="text-gray-300">
                    <span className="text-gray-500 font-semibold">AI Reasoning: </span>
                    {lead.intent_reasoning}
                  </p>
                )}
                {lead.intent_confidence != null && (
                  <p className="text-gray-400 text-xs">
                    Confidence: {(lead.intent_confidence * 100).toFixed(0)}%
                  </p>
                )}
                {lead.error_message && (
                  <p className="text-red-400 text-xs">
                    <span className="font-semibold">Error: </span>{lead.error_message}
                  </p>
                )}
              </div>
            </div>
          </div>

          {/* Actions */}
          <div className="flex gap-2 mt-4 flex-wrap">
            <button
              onClick={() => handleAction(lead.id, 'approve')}
              className="px-3 py-1.5 bg-green-700 hover:bg-green-600 text-white text-xs font-medium rounded-lg transition-colors"
            >
              Approve & Send
            </button>
            <button
              onClick={() => onSelectLead(lead.id)}
              className="px-3 py-1.5 bg-indigo-700 hover:bg-indigo-600 text-white text-xs font-medium rounded-lg transition-colors"
            >
              Edit Draft
            </button>
            <button
              onClick={() => handleAction(lead.id, 'skip')}
              className="px-3 py-1.5 bg-gray-700 hover:bg-gray-600 text-white text-xs font-medium rounded-lg transition-colors"
            >
              Skip
            </button>
            <button
              onClick={() => handleAction(lead.id, 'close')}
              className="px-3 py-1.5 bg-red-900 hover:bg-red-800 text-white text-xs font-medium rounded-lg transition-colors"
            >
              Close Lost
            </button>
            <button
              onClick={() => onSelectLead(lead.id)}
              className="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 text-gray-300 text-xs font-medium rounded-lg transition-colors ml-auto"
            >
              View Full Detail →
            </button>
          </div>
        </div>
      ))}
    </div>
  )
}
