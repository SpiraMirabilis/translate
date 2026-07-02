import { useState } from 'react'
import { X, Loader2, Globe, FileText } from 'lucide-react'
import { api } from '../../services/api'
import { localIso } from '../PublishMenu'

const INTERVALS = [
  { label: 'No stagger — all at once', hours: 0 },
  { label: 'Every hour', hours: 1 },
  { label: 'Every 6 hours', hours: 6 },
  { label: 'Every 12 hours', hours: 12 },
  { label: 'Every day', hours: 24 },
  { label: 'Every 2 days', hours: 48 },
  { label: 'Every 3 days', hours: 72 },
  { label: 'Every week', hours: 168 },
]

const fmt = (d) => d.toLocaleString([], {
  month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
})

/**
 * Batch publish/schedule for selected chapters. Default = publish everything
 * immediately; optionally set a start time and a per-chapter stagger for a
 * drip release (ch. N at start, N+1 at start+interval, …).
 */
export default function BatchPublishModal({ bookId, chapters, onClose, onDone }) {
  const [startAt, setStartAt] = useState('') // empty = now
  const [intervalHours, setIntervalHours] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  const nums = [...chapters].sort((a, b) => a - b)
  const start = startAt ? new Date(startAt) : new Date()
  const first = start
  const last = new Date(start.getTime() + intervalHours * 3600_000 * (nums.length - 1))

  const run = async (unpublish = false) => {
    setBusy(true)
    setError(null)
    try {
      await api.batchPublish(bookId, unpublish
        ? { chapters: nums, unpublish: true }
        : {
          chapters: nums,
          published_at: startAt ? localIso(new Date(startAt)) : null,
          interval_hours: intervalHours,
        })
      onDone()
    } catch (e) {
      setError(e.message)
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="card w-full max-w-md shadow-2xl">
        <div className="flex items-center justify-between px-5 py-3.5 border-b border-slate-700/60">
          <h2 className="font-semibold text-slate-200 text-sm">
            Publish {nums.length} chapter{nums.length === 1 ? '' : 's'}
          </h2>
          <button className="btn-ghost p-1" onClick={onClose}><X size={15} /></button>
        </div>

        <div className="px-5 py-4 space-y-3.5">
          <div>
            <label className="label">Start</label>
            <input
              type="datetime-local"
              className="input text-sm"
              value={startAt}
              onChange={(e) => setStartAt(e.target.value)}
            />
            <p className="text-xs text-slate-500 mt-1">Leave empty to publish immediately.</p>
          </div>

          <div>
            <label className="label">Stagger</label>
            <select
              className="input text-sm"
              value={intervalHours}
              onChange={(e) => setIntervalHours(parseFloat(e.target.value))}
            >
              {INTERVALS.map((i) => (
                <option key={i.hours} value={i.hours}>{i.label}</option>
              ))}
            </select>
          </div>

          {nums.length > 1 && intervalHours > 0 && (
            <p className="text-xs text-slate-400">
              Ch. {nums[0]} at <span className="text-slate-200">{fmt(first)}</span> …
              ch. {nums[nums.length - 1]} at <span className="text-slate-200">{fmt(last)}</span>
            </p>
          )}

          {error && <p className="text-xs text-rose-400">{error}</p>}
        </div>

        <div className="px-5 py-3 border-t border-slate-700/60 flex items-center justify-between">
          <button
            className="btn-ghost text-xs text-slate-500 hover:text-amber-300 flex items-center gap-1"
            disabled={busy}
            onClick={() => run(true)}
            title="Set all selected chapters back to unpublished drafts"
          >
            <FileText size={12} /> Back to draft
          </button>
          <div className="flex gap-2">
            <button className="btn-secondary text-sm" onClick={onClose}>Cancel</button>
            <button
              className="btn-primary flex items-center gap-1.5 text-sm"
              disabled={busy}
              onClick={() => run(false)}
            >
              {busy ? <Loader2 size={13} className="animate-spin" /> : <Globe size={13} />}
              {startAt || intervalHours > 0 ? 'Schedule' : 'Publish now'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
