import { useEffect, useState } from 'react'
import axios from 'axios'

interface Metrics {
  total_leads: number
  in_progress: number
  emails_sent: number
  replies: number
  meetings_booked: number
  pending_review: number
  estimated_cost_usd: number
}

interface MetricCardProps {
  label: string
  value: string | number
  color?: string
}

function MetricCard({ label, value, color = 'text-white' }: MetricCardProps) {
  return (
    <div className="bg-gray-900 border border-gray-800 rounded-xl px-5 py-4 flex-1 min-w-0">
      <p className="text-xs text-gray-500 uppercase tracking-wide font-medium mb-1">{label}</p>
      <p className={`text-2xl font-bold ${color}`}>{value}</p>
    </div>
  )
}

export default function MetricsBar({ eventTick = 0 }: { eventTick?: number }) {
  const [metrics, setMetrics] = useState<Metrics | null>(null)

  const fetchMetrics = async () => {
    try {
      const res = await axios.get('/api/metrics')
      setMetrics(res.data)
    } catch {
      // ignore
    }
  }

  // SSE events drive refresh; the interval is only a fallback
  useEffect(() => {
    fetchMetrics()
  }, [eventTick])

  useEffect(() => {
    const interval = setInterval(fetchMetrics, 30000)
    return () => clearInterval(interval)
  }, [])

  if (!metrics) {
    return (
      <div className="px-6 py-3 border-b border-gray-800">
        <div className="flex gap-3 animate-pulse">
          {Array.from({ length: 7 }).map((_, i) => (
            <div key={i} className="flex-1 h-16 bg-gray-800 rounded-xl" />
          ))}
        </div>
      </div>
    )
  }

  return (
    <div className="px-6 py-3 border-b border-gray-800 bg-gray-950">
      <div className="flex gap-3 overflow-x-auto pb-1">
        <MetricCard label="Total Leads" value={metrics.total_leads} />
        <MetricCard label="In Progress" value={metrics.in_progress} color="text-blue-400" />
        <MetricCard label="Emails Sent" value={metrics.emails_sent} color="text-purple-400" />
        <MetricCard label="Replies" value={metrics.replies} color="text-green-400" />
        <MetricCard label="Meetings Booked" value={metrics.meetings_booked} color="text-teal-400" />
        <MetricCard
          label="Pending Review"
          value={metrics.pending_review}
          color={metrics.pending_review > 0 ? 'text-orange-400' : 'text-white'}
        />
        <MetricCard
          label="Est. Cost (USD)"
          value={`$${metrics.estimated_cost_usd.toFixed(2)}`}
          color="text-yellow-400"
        />
      </div>
    </div>
  )
}
