import { useEffect, useState } from 'react'
import axios from 'axios'

interface Lead {
  id: string
  company_name: string
  website?: string
  status: string
  company_summary?: string
  industry?: string
  employee_size_estimate?: string
  pain_points?: string[]
  recent_news?: string[]
  research_confidence_score?: number
  contact_name?: string
  contact_email?: string
  contact_role?: string
  subject_line?: string
  email_body?: string
  personalisation_fact_used?: string
  word_count?: number
  human_approval_required?: boolean
  sent_at?: string
  reply_text?: string
  intent?: string
  intent_confidence?: number
  intent_reasoning?: string
  booking_email_draft?: string
  error_message?: string
}

interface Props {
  leadId: string
  onClose: () => void
}

export default function LeadDetail({ leadId, onClose }: Props) {
  const [lead, setLead] = useState<Lead | null>(null)
  const [editDraft, setEditDraft] = useState('')
  const [editMode, setEditMode] = useState(false)
  const [submitting, setSubmitting] = useState(false)

  const fetchLead = async () => {
    try {
      const res = await axios.get(`/api/leads/${leadId}`)
      setLead(res.data)
    } catch {
      // ignore
    }
  }

  useEffect(() => {
    fetchLead()
    const interval = setInterval(fetchLead, 5000)
    return () => clearInterval(interval)
  }, [leadId])

  const handleAction = async (action: string, edited_draft?: string) => {
    setSubmitting(true)
    try {
      await axios.patch(`/api/leads/${leadId}/human-action`, { action, edited_draft })
      await fetchLead()
      if (action === 'edit') setEditMode(false)
    } catch (e: any) {
      alert(e?.response?.data?.detail || 'Action failed')
    } finally {
      setSubmitting(false)
    }
  }

  if (!lead) {
    return (
      <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center">
        <div className="bg-gray-900 rounded-2xl p-8 text-gray-400">Loading...</div>
      </div>
    )
  }

  const isReview = lead.status === 'HUMAN_REVIEW'

  return (
    <div className="fixed inset-0 bg-black/70 z-50 flex items-end sm:items-center justify-center p-4">
      <div className="bg-gray-900 border border-gray-800 rounded-2xl w-full max-w-2xl max-h-[90vh] overflow-y-auto shadow-2xl">
        {/* Header */}
        <div className="sticky top-0 bg-gray-900 border-b border-gray-800 px-6 py-4 flex items-center justify-between z-10">
          <div>
            <h2 className="text-lg font-semibold text-white">{lead.company_name}</h2>
            <p className="text-xs text-gray-500 mt-0.5">{lead.status.replace(/_/g, ' ')}</p>
          </div>
          <button
            onClick={onClose}
            className="text-gray-500 hover:text-white text-xl leading-none transition-colors"
          >
            ✕
          </button>
        </div>

        <div className="px-6 py-5 space-y-6">
          {/* Human Approval Warning */}
          {lead.human_approval_required && (
            <div className="bg-orange-950 border border-orange-700 rounded-xl p-4 text-orange-300 text-sm">
              <strong>Human approval required.</strong> The AI could not produce a valid email draft after 2 attempts. Please review and edit before sending.
            </div>
          )}

          {/* Error */}
          {lead.error_message && (
            <div className="bg-red-950 border border-red-700 rounded-xl p-4 text-red-300 text-sm">
              <strong>Error:</strong> {lead.error_message}
            </div>
          )}

          {/* Company Info */}
          <section>
            <h3 className="text-xs uppercase text-gray-500 font-semibold tracking-wide mb-3">Company Research</h3>
            <div className="grid grid-cols-2 gap-3 text-sm">
              <div className="bg-gray-800 rounded-lg p-3">
                <p className="text-gray-500 text-xs mb-1">Industry</p>
                <p className="text-white">{lead.industry || '—'}</p>
              </div>
              <div className="bg-gray-800 rounded-lg p-3">
                <p className="text-gray-500 text-xs mb-1">Size</p>
                <p className="text-white">{lead.employee_size_estimate || '—'}</p>
              </div>
              <div className="bg-gray-800 rounded-lg p-3">
                <p className="text-gray-500 text-xs mb-1">Research Confidence</p>
                <p className="text-white">
                  {lead.research_confidence_score != null
                    ? `${(lead.research_confidence_score * 100).toFixed(0)}%`
                    : '—'}
                </p>
              </div>
              <div className="bg-gray-800 rounded-lg p-3">
                <p className="text-gray-500 text-xs mb-1">Website</p>
                <p className="text-white truncate">{lead.website || '—'}</p>
              </div>
            </div>
            {lead.company_summary && (
              <p className="mt-3 text-sm text-gray-300 leading-relaxed">{lead.company_summary}</p>
            )}
            {lead.pain_points && lead.pain_points.length > 0 && (
              <div className="mt-3">
                <p className="text-gray-500 text-xs mb-2">Pain Points</p>
                <ul className="list-disc list-inside space-y-1 text-sm text-gray-300">
                  {lead.pain_points.map((p, i) => <li key={i}>{p}</li>)}
                </ul>
              </div>
            )}
            {lead.recent_news && lead.recent_news.length > 0 && (
              <div className="mt-3">
                <p className="text-gray-500 text-xs mb-2">Recent News</p>
                <ul className="list-disc list-inside space-y-1 text-sm text-gray-300">
                  {lead.recent_news.map((n, i) => <li key={i}>{n}</li>)}
                </ul>
              </div>
            )}
          </section>

          {/* Contact */}
          {(lead.contact_name || lead.contact_email) && (
            <section>
              <h3 className="text-xs uppercase text-gray-500 font-semibold tracking-wide mb-3">Contact</h3>
              <div className="bg-gray-800 rounded-xl p-4 text-sm space-y-2">
                <p><span className="text-gray-500">Name: </span><span className="text-white">{lead.contact_name || '—'}</span></p>
                <p><span className="text-gray-500">Email: </span><span className="text-white">{lead.contact_email || '—'}</span></p>
                <p><span className="text-gray-500">Role: </span><span className="text-white">{lead.contact_role || '—'}</span></p>
              </div>
            </section>
          )}

          {/* Email Draft */}
          {(lead.subject_line || lead.email_body) && (
            <section>
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-xs uppercase text-gray-500 font-semibold tracking-wide">Email Draft</h3>
                {isReview && !editMode && (
                  <button
                    onClick={() => {
                      setEditDraft(`${lead.subject_line}\n\n${lead.email_body}`)
                      setEditMode(true)
                    }}
                    className="text-xs text-indigo-400 hover:text-indigo-300 transition-colors"
                  >
                    Edit
                  </button>
                )}
              </div>
              {editMode ? (
                <div className="space-y-3">
                  <textarea
                    value={editDraft}
                    onChange={(e) => setEditDraft(e.target.value)}
                    rows={10}
                    className="w-full bg-gray-800 border border-gray-700 rounded-xl p-4 text-sm text-gray-100 font-mono resize-none focus:outline-none focus:ring-2 focus:ring-indigo-500"
                  />
                  <div className="flex gap-2">
                    <button
                      onClick={() => handleAction('edit', editDraft)}
                      disabled={submitting}
                      className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white text-sm rounded-lg transition-colors"
                    >
                      Save & Send
                    </button>
                    <button
                      onClick={() => setEditMode(false)}
                      className="px-4 py-2 bg-gray-700 hover:bg-gray-600 text-white text-sm rounded-lg transition-colors"
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              ) : (
                <div className="bg-gray-800 rounded-xl p-4 text-sm space-y-3">
                  <p className="font-semibold text-white">Subject: {lead.subject_line}</p>
                  <pre className="whitespace-pre-wrap text-gray-300 font-sans leading-relaxed">{lead.email_body}</pre>
                  {lead.personalisation_fact_used && (
                    <p className="text-xs text-indigo-400 border-t border-gray-700 pt-3">
                      Personalisation: {lead.personalisation_fact_used}
                    </p>
                  )}
                </div>
              )}
            </section>
          )}

          {/* Reply */}
          {lead.reply_text && (
            <section>
              <h3 className="text-xs uppercase text-gray-500 font-semibold tracking-wide mb-3">Reply Received</h3>
              <div className="bg-gray-800 rounded-xl p-4 text-sm space-y-3">
                <pre className="whitespace-pre-wrap text-gray-300 font-sans leading-relaxed">{lead.reply_text}</pre>
                {lead.intent && (
                  <div className="border-t border-gray-700 pt-3 grid grid-cols-3 gap-2 text-xs">
                    <div>
                      <p className="text-gray-500 mb-1">Intent</p>
                      <p className="text-white font-semibold">{lead.intent}</p>
                    </div>
                    <div>
                      <p className="text-gray-500 mb-1">Confidence</p>
                      <p className="text-white font-semibold">
                        {lead.intent_confidence != null
                          ? `${(lead.intent_confidence * 100).toFixed(0)}%`
                          : '—'}
                      </p>
                    </div>
                    <div>
                      <p className="text-gray-500 mb-1">Reasoning</p>
                      <p className="text-gray-300">{lead.intent_reasoning || '—'}</p>
                    </div>
                  </div>
                )}
              </div>
            </section>
          )}

          {/* Booking Draft */}
          {lead.booking_email_draft && (
            <section>
              <h3 className="text-xs uppercase text-gray-500 font-semibold tracking-wide mb-3">Meeting Booking Draft</h3>
              <div className="bg-teal-950 border border-teal-800 rounded-xl p-4 text-sm">
                <pre className="whitespace-pre-wrap text-teal-200 font-sans leading-relaxed">{lead.booking_email_draft}</pre>
              </div>
            </section>
          )}

          {/* Human Review Actions */}
          {isReview && !editMode && (
            <section>
              <h3 className="text-xs uppercase text-orange-500 font-semibold tracking-wide mb-3">Human Review Required</h3>
              <div className="flex gap-2 flex-wrap">
                <button
                  onClick={() => handleAction('approve')}
                  disabled={submitting}
                  className="px-4 py-2 bg-green-700 hover:bg-green-600 disabled:opacity-50 text-white text-sm rounded-lg font-medium transition-colors"
                >
                  Approve & Send
                </button>
                <button
                  onClick={() => {
                    setEditDraft(`${lead.subject_line || ''}\n\n${lead.email_body || ''}`)
                    setEditMode(true)
                  }}
                  disabled={submitting}
                  className="px-4 py-2 bg-indigo-700 hover:bg-indigo-600 disabled:opacity-50 text-white text-sm rounded-lg font-medium transition-colors"
                >
                  Edit Draft
                </button>
                <button
                  onClick={() => handleAction('skip')}
                  disabled={submitting}
                  className="px-4 py-2 bg-gray-700 hover:bg-gray-600 disabled:opacity-50 text-white text-sm rounded-lg font-medium transition-colors"
                >
                  Skip
                </button>
                <button
                  onClick={() => handleAction('close')}
                  disabled={submitting}
                  className="px-4 py-2 bg-red-900 hover:bg-red-800 disabled:opacity-50 text-white text-sm rounded-lg font-medium transition-colors"
                >
                  Mark Closed
                </button>
              </div>
            </section>
          )}
        </div>
      </div>
    </div>
  )
}
