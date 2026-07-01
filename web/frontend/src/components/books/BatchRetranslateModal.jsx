import { useState } from 'react'
import { api } from '../../services/api'
import { X, Loader2, Sparkles } from 'lucide-react'

export default function BatchRetranslateModal({ bookId, chapters, onClose, onDone }) {
  const [reason, setReason] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState(null)
  const [result, setResult] = useState(null) // { queued, errors }

  const sorted = [...chapters].sort((a, b) => a - b)
  const firstCh = sorted[0]
  const lastCh = sorted[sorted.length - 1]
  const rangeLabel = sorted.length === 1
    ? `Ch. ${firstCh}`
    : (sorted.length === (lastCh - firstCh + 1) && sorted.every((n, i) => n === firstCh + i))
      ? `Chs. ${firstCh}–${lastCh}`
      : `${sorted.length} chapters`

  const handleSubmit = async () => {
    setSubmitting(true)
    setError(null)
    try {
      const res = await api.batchRequeue(bookId, sorted, reason.trim() || null)
      setResult({ queued: res.queued || 0, errors: res.errors || [] })
    } catch (e) {
      setError(e.message)
      setSubmitting(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="card w-full max-w-md p-6 space-y-4 shadow-2xl">
        <div className="flex items-center justify-between">
          <h2 className="font-semibold text-slate-200">Retranslate Chapters</h2>
          <button className="btn-ghost p-1" onClick={onClose}><X size={16} /></button>
        </div>

        <p className="text-sm text-slate-400">
          Queue <span className="text-slate-200">{rangeLabel}</span>
          {' '}for retranslation. Chapters will be processed in ascending order. Existing translations will be overwritten.
        </p>

        {!result ? (
          <>
            <div className="space-y-3">
              <div>
                <label className="label">Reason for retranslation <span className="text-slate-500 font-normal">(optional)</span></label>
                <textarea
                  className="input w-full resize-y min-h-[72px]"
                  rows={3}
                  value={reason}
                  onChange={e => setReason(e.target.value)}
                  placeholder="e.g. Previous version garbled cultivation terminology; keep honorifics."
                  disabled={submitting}
                />
                <p className="text-xs text-slate-500 mt-1">
                  Applied to every chapter in this batch. Appended to the system prompt so the model knows what to fix.
                </p>
              </div>
            </div>

            {error && <p className="text-rose-400 text-sm">{error}</p>}

            <div className="flex justify-end gap-2">
              <button className="btn-secondary" onClick={onClose} disabled={submitting}>Cancel</button>
              <button
                className="btn-primary flex items-center gap-1.5"
                onClick={handleSubmit}
                disabled={submitting}
              >
                {submitting ? <Loader2 size={13} className="animate-spin" /> : <Sparkles size={13} />}
                Queue {sorted.length} for Retranslation
              </button>
            </div>
          </>
        ) : (
          <div className="space-y-3">
            <p className="text-sm text-emerald-400">
              Queued {result.queued} chapter(s) for retranslation.
            </p>
            {result.errors.length > 0 && (
              <div className="text-xs text-amber-400 space-y-1">
                {result.errors.map((err, i) => <p key={i}>• {err}</p>)}
              </div>
            )}
            <div className="flex justify-end">
              <button className="btn-primary" onClick={onDone}>Done</button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
