import { useState } from 'react'
import { api } from '../../services/api'
import { X, Loader2, Sparkles } from 'lucide-react'

export default function RetranslateModal({ bookId, chapterNum, chapterTitle, onClose }) {
  const [reason, setReason] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState(null)
  const [done, setDone] = useState(false)

  const handleSubmit = async () => {
    setSubmitting(true)
    setError(null)
    try {
      // Fetch the chapter's original Chinese text
      const chapter = await api.getChapter(bookId, chapterNum)
      const untranslated = chapter.untranslated || []
      if (!untranslated.length) {
        setError('No source text found for this chapter.')
        setSubmitting(false)
        return
      }
      // Add to queue
      await api.addToQueue({
        text: untranslated.join('\n'),
        book_id: bookId,
        chapter_number: chapterNum,
        title: chapterTitle,
        priority: true,
        retranslation_reason: reason.trim() || null,
      })
      setDone(true)
    } catch (e) {
      setError(e.message)
      setSubmitting(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="card w-full max-w-md p-6 space-y-4 shadow-2xl">
        <div className="flex items-center justify-between">
          <h2 className="font-semibold text-slate-200">Retranslate Chapter</h2>
          <button className="btn-ghost p-1" onClick={onClose}><X size={16} /></button>
        </div>

        <p className="text-sm text-slate-400">
          Queue <span className="text-slate-200">Ch. {chapterNum}</span>
          {chapterTitle && <> — <span className="text-slate-300">{chapterTitle}</span></>}
          {' '}for retranslation. The existing translation will be overwritten when the queue item is processed.
        </p>

        {!done ? (
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
                  Appended to the system prompt so the model knows what to fix.
                </p>
              </div>
            </div>

            {error && <p className="text-rose-400 text-sm">{error}</p>}

            <div className="flex justify-end gap-2">
              <button className="btn-secondary" onClick={onClose}>Cancel</button>
              <button
                className="btn-primary flex items-center gap-1.5"
                onClick={handleSubmit}
                disabled={submitting}
              >
                {submitting ? <Loader2 size={13} className="animate-spin" /> : <Sparkles size={13} />}
                Queue for Retranslation
              </button>
            </div>
          </>
        ) : (
          <div className="space-y-3">
            <p className="text-sm text-emerald-400">
              Chapter {chapterNum} has been added to the translation queue. Go to the Queue page to process it.
            </p>
            <div className="flex justify-end">
              <button className="btn-primary" onClick={onClose}>Done</button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
