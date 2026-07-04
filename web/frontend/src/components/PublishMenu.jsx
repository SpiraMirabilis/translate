import { useState, useEffect } from 'react'
import { Globe, Clock, FileText, Loader2, ChevronDown } from 'lucide-react'
import { api } from '../services/api'

/** Naive local ISO timestamp — matches the server's translation_date /
 * published_at convention (never UTC/toISOString, which would break the
 * lexicographic time comparisons). */
export const localIso = (d = new Date()) => {
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}` +
    `T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

export function publishStatus(publishedAt) {
  if (!publishedAt) return 'draft'
  return publishedAt > localIso() ? 'scheduled' : 'published'
}

const STATUS_STYLE = {
  draft: 'bg-amber-500/15 text-amber-300 border-amber-600/40',
  scheduled: 'bg-sky-500/15 text-sky-300 border-sky-600/40',
  published: 'bg-emerald-500/15 text-emerald-300 border-emerald-600/40',
}
const STATUS_ICON = { draft: FileText, scheduled: Clock, published: Globe }

/**
 * Publish-state control for a single chapter: status chip + dropdown with
 * Publish now / Schedule (datetime-local) / Back to draft. Self-contained —
 * calls the publish API itself and reports the stored value via onChanged.
 * `beforePublish` (optional, async) runs first — e.g. save unsaved edits.
 */
export default function PublishMenu({ bookId, chapterNum, publishedAt, onChanged, beforePublish }) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [scheduleAt, setScheduleAt] = useState('')

  const status = publishStatus(publishedAt)
  const Icon = STATUS_ICON[status]

  // Esc closes the dropdown; data-esc-guard (below) keeps other Esc handlers
  // (e.g. the write editor's exit-focus-mode) from also firing.
  useEffect(() => {
    if (!open) return undefined
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  const apply = async (value) => {
    setBusy(true)
    setError(null)
    try {
      if (beforePublish && !(await beforePublish())) return
      const res = await api.publishChapter(bookId, chapterNum, value)
      onChanged(res.published_at)
      setOpen(false)
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="relative">
      <button
        type="button"
        className={`flex items-center gap-1.5 px-2 py-1 rounded border text-xs font-medium ${STATUS_STYLE[status]}`}
        onClick={() => setOpen((v) => !v)}
        title={status === 'scheduled'
          ? `Scheduled for ${new Date(publishedAt).toLocaleString()}`
          : status === 'published'
            ? `Published ${new Date(publishedAt).toLocaleString()}`
            : 'Draft — not visible on the public site'}
      >
        {busy ? <Loader2 size={11} className="animate-spin" /> : <Icon size={11} />}
        {status === 'scheduled'
          ? `Scheduled · ${new Date(publishedAt).toLocaleDateString()} ${new Date(publishedAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`
          : status.charAt(0).toUpperCase() + status.slice(1)}
        <ChevronDown size={11} />
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-30" onClick={() => setOpen(false)} />
          <div data-esc-guard className="absolute right-0 top-full mt-1 z-40 w-64 rounded border border-slate-700 bg-slate-800 shadow-xl p-3 space-y-2.5">
            {status !== 'published' && (
              <button
                type="button"
                className="btn-primary w-full text-sm flex items-center justify-center gap-1.5"
                disabled={busy}
                onClick={() => apply(localIso())}
              >
                <Globe size={13} /> Publish now
              </button>
            )}
            <div className="space-y-1.5">
              <label className="text-xs text-slate-400">
                {status === 'scheduled' ? 'Reschedule for' : 'Schedule for'}
              </label>
              <div className="flex gap-1.5">
                <input
                  type="datetime-local"
                  className="input text-xs flex-1"
                  value={scheduleAt}
                  onChange={(e) => setScheduleAt(e.target.value)}
                />
                <button
                  type="button"
                  className="btn-secondary text-xs px-2"
                  disabled={busy || !scheduleAt}
                  onClick={() => apply(scheduleAt)}
                >
                  Set
                </button>
              </div>
            </div>
            {status !== 'draft' && (
              <button
                type="button"
                className="btn-secondary w-full text-xs"
                disabled={busy}
                onClick={() => apply(null)}
              >
                {status === 'scheduled' ? 'Cancel schedule (back to draft)' : 'Unpublish (back to draft)'}
              </button>
            )}
            {error && <p className="text-xs text-rose-400">{error}</p>}
          </div>
        </>
      )}
    </div>
  )
}
