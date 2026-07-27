import { useEffect, useState } from 'react'
import axios from 'axios'

interface Lead {
  id: string
  company_name: string
  contact_name?: string
  contact_email?: string
  status: string
  sent_at?: string
  updated_at?: string
}

const STATUS_COLORS: Record<string, string> = {
  UPLOADED: 'bg-gray-700 text-gray-300',
  RESEARCH_PENDING: 'bg-blue-900 text-blue-300',
  RESEARCH_COMPLETE: 'bg-blue-800 text-blue-200',
  CONTACT_FOUND: 'bg-yellow-900 text-yellow-300',
  DRAFT_READY: 'bg-yellow-800 text-yellow-200',
  SENT: 'bg-purple-900 text-purple-300',
  FOLLOW_UP_SENT: 'bg-purple-800 text-purple-200',
  REPLY_RECEIVED: 'bg-green-900 text-green-300',
  INTERESTED: 'bg-green-800 text-green-200',
  BOOKING_DRAFTED: 'bg-teal-900 text-teal-300',
  CLOSED_LOST: 'bg-red-900 text-red-300',
  NO_CONTACT_FOUND: 'bg-red-900 text-red-300',
  RESEARCH_FAILED: 'bg-red-900 text-red-300',
  INVALID_EMAIL: 'bg-red-900 text-red-300',
  HUMAN_REVIEW: 'bg-orange-900 text-orange-300',
}

const ALL_STATUSES = [
  'ALL',
  'UPLOADED',
  'RESEARCH_PENDING',
  'RESEARCH_COMPLETE',
  'CONTACT_FOUND',
  'DRAFT_READY',
  'SENT',
  'FOLLOW_UP_SENT',
  'REPLY_RECEIVED',
  'INTERESTED',
  'BOOKING_DRAFTED',
  'HUMAN_REVIEW',
  'CLOSED_LOST',
  'NO_CONTACT_FOUND',
  'RESEARCH_FAILED',
  'INVALID_EMAIL',
]

function StatusBadge({ status }: { status: string }) {
  const cls = STATUS_COLORS[status] || 'bg-gray-700 text-gray-300'
  return (
    <span className={`inline-block px-2 py-0.5 rounded text-xs font-semibold ${cls}`}>
      {status.replace(/_/g, ' ')}
    </span>
  )
}

function formatDate(val?: string) {
  if (!val) return '—'
  return new Date(val).toLocaleString()
}

interface Props {
  onSelectLead: (id: string) => void
  eventTick?: number
}

export default function LeadTable({ onSelectLead, eventTick = 0 }: Props) {
  const [leads, setLeads] = useState<Lead[]>([])
  const [statusFilter, setStatusFilter] = useState('ALL')
  const [loading, setLoading] = useState(true)

  const fetchLeads = async () => {
    try {
      const params = statusFilter !== 'ALL' ? { status: statusFilter } : {}
      const res = await axios.get('/api/leads', { params })
      setLeads(res.data.leads)
    } catch {
      // ignore
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    setLoading(true)
    fetchLeads()
  }, [statusFilter])

  // SSE events drive refresh; the interval is only a fallback
  useEffect(() => {
    if (eventTick > 0) fetchLeads()
  }, [eventTick])

  useEffect(() => {
    const interval = setInterval(fetchLeads, 30000)
    return () => clearInterval(interval)
  }, [statusFilter])

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
      <div className="flex items-center justify-between px-5 py-4 border-b border-gray-800">
        <h2 className="text-base font-semibold text-white">Leads</h2>
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="bg-gray-800 border border-gray-700 text-gray-300 text-sm rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-indigo-500"
        >
          {ALL_STATUSES.map((s) => (
            <option key={s} value={s}>
              {s.replace(/_/g, ' ')}
            </option>
          ))}
        </select>
      </div>

      {loading ? (
        <div className="p-8 text-center text-gray-500">Loading...</div>
      ) : leads.length === 0 ? (
        <div className="p-8 text-center text-gray-500">No leads found.</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-gray-500 text-xs uppercase border-b border-gray-800">
                <th className="px-5 py-3 font-medium">Company</th>
                <th className="px-5 py-3 font-medium">Contact</th>
                <th className="px-5 py-3 font-medium">Status</th>
                <th className="px-5 py-3 font-medium">Sent At</th>
                <th className="px-5 py-3 font-medium">Last Updated</th>
              </tr>
            </thead>
            <tbody>
              {leads.map((lead) => (
                <tr
                  key={lead.id}
                  onClick={() => onSelectLead(lead.id)}
                  className="border-b border-gray-800 hover:bg-gray-800 cursor-pointer transition-colors"
                >
                  <td className="px-5 py-3 font-medium text-white">{lead.company_name}</td>
                  <td className="px-5 py-3 text-gray-400">
                    {lead.contact_name || lead.contact_email || '—'}
                  </td>
                  <td className="px-5 py-3">
                    <StatusBadge status={lead.status} />
                  </td>
                  <td className="px-5 py-3 text-gray-500">{formatDate(lead.sent_at)}</td>
                  <td className="px-5 py-3 text-gray-500">{formatDate(lead.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
