import { useEffect, useState } from 'react'
import axios from 'axios'

interface Profile {
  company_name: string
  product_description: string
  value_proposition: string
  sender_name: string
  target_customer?: string
  sender_title?: string
  meeting_link?: string
  tone?: string
}

const EMPTY: Profile = {
  company_name: '', product_description: '', value_proposition: '',
  sender_name: '', target_customer: '', sender_title: '', meeting_link: '', tone: '',
}

const FIELDS: { key: keyof Profile; label: string; hint: string; required?: boolean; textarea?: boolean }[] = [
  { key: 'company_name', label: 'Your company', hint: 'Who the outreach is from', required: true },
  { key: 'product_description', label: 'What you sell', hint: 'The product/service in 1–3 sentences — the AI writes from this, never invents', required: true, textarea: true },
  { key: 'value_proposition', label: 'Value proposition', hint: 'The concrete benefit a customer gets', required: true, textarea: true },
  { key: 'target_customer', label: 'Typical customer', hint: 'e.g. "B2B SaaS companies, 50–500 employees"' },
  { key: 'sender_name', label: 'Sender name', hint: 'Emails are signed with this', required: true },
  { key: 'sender_title', label: 'Sender title', hint: 'e.g. "Founder" or "Account Executive"' },
  { key: 'meeting_link', label: 'Booking link', hint: 'Calendly-style link used in calls to action (optional)' },
  { key: 'tone', label: 'Tone', hint: 'e.g. "friendly and direct", "formal" (optional)' },
]

export default function SellerProfile() {
  const [profile, setProfile] = useState<Profile>(EMPTY)
  const [configured, setConfigured] = useState(false)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState<string | null>(null)

  useEffect(() => {
    axios.get('/api/profile').then((res) => {
      if (res.data && res.data.company_name) {
        setProfile({ ...EMPTY, ...res.data })
        setConfigured(true)
      }
    }).catch(() => {})
  }, [])

  const save = async () => {
    setSaving(true)
    setMessage(null)
    try {
      await axios.put('/api/profile', profile)
      setConfigured(true)
      setMessage('Saved. New uploads will use this context.')
    } catch (e: any) {
      const detail = e?.response?.data?.detail
      setMessage(typeof detail === 'string' ? detail : 'Save failed — the four required fields must be filled.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="max-w-2xl">
      <div className="mb-5">
        <h2 className="text-lg font-semibold text-white">Seller Profile</h2>
        <p className="text-sm text-gray-500 mt-1">
          What the pipeline knows about <span className="text-gray-300">you</span>. Research scores
          prospect pain points against this, and every email is written from it — uploads are
          blocked until it exists.
        </p>
        {!configured && (
          <p className="text-xs text-orange-300 bg-orange-900/30 border border-orange-900/60 rounded-lg px-3 py-2 mt-3">
            Not configured yet — CSV uploads are blocked until you save this.
          </p>
        )}
      </div>

      <div className="space-y-4">
        {FIELDS.map((f) => (
          <div key={f.key}>
            <label className="block text-sm font-medium text-gray-300 mb-1">
              {f.label} {f.required && <span className="text-orange-400">*</span>}
            </label>
            {f.textarea ? (
              <textarea
                value={profile[f.key] || ''}
                onChange={(e) => setProfile({ ...profile, [f.key]: e.target.value })}
                rows={2}
                className="w-full bg-gray-900 border border-gray-800 rounded-lg px-3 py-2 text-sm text-gray-200 focus:outline-none focus:ring-2 focus:ring-indigo-500"
              />
            ) : (
              <input
                value={profile[f.key] || ''}
                onChange={(e) => setProfile({ ...profile, [f.key]: e.target.value })}
                className="w-full bg-gray-900 border border-gray-800 rounded-lg px-3 py-2 text-sm text-gray-200 focus:outline-none focus:ring-2 focus:ring-indigo-500"
              />
            )}
            <p className="text-xs text-gray-600 mt-1">{f.hint}</p>
          </div>
        ))}
      </div>

      <div className="flex items-center gap-3 mt-6">
        <button
          onClick={save}
          disabled={saving}
          className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white text-sm font-medium rounded-lg transition-colors"
        >
          {saving ? 'Saving…' : 'Save profile'}
        </button>
        {message && <span className="text-sm text-gray-400">{message}</span>}
      </div>
    </div>
  )
}
