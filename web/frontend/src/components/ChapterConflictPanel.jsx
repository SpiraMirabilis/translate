/**
 * ChapterConflictPanel
 *
 * Modal that appears before translating a queue item whose chapter_number
 * collides with an already-translated chapter, and whose source text differs.
 * Shows the existing chapter and the incoming queue item side-by-side and
 * asks the user whether to overwrite the existing chapter or skip the item.
 */
import { useState, useCallback, useEffect } from 'react'
import { api } from '../services/api'
import { AlertTriangle, Check, X } from 'lucide-react'

export default function ChapterConflictPanel({
  bookId,
  chapterNumber,
  bookTitle,
  existingTitle,
  existingUntranslated,
  newTitle,
  newUntranslated,
  onDone,
}) {
  const [submitting, setSubmitting] = useState(false)

  // Re-fetch the payload from the API on mount as a safety net — mirrors
  // the JsonFixPanel pattern so the modal still works after a page refresh.
  const [existing, setExisting] = useState(existingUntranslated || [])
  const [incoming, setIncoming] = useState(newUntranslated || [])
  const [eTitle, setETitle] = useState(existingTitle || '')
  const [nTitle, setNTitle] = useState(newTitle || '')
  const [bTitle, setBTitle] = useState(bookTitle || '')
  const [chNum, setChNum] = useState(chapterNumber)

  useEffect(() => {
    api.getJobStatus().then(d => {
      if (d.pending_chapter_conflict) {
        const p = d.pending_chapter_conflict
        if (Array.isArray(p.existing_untranslated)) setExisting(p.existing_untranslated)
        if (Array.isArray(p.new_untranslated))      setIncoming(p.new_untranslated)
        if (p.existing_title)                       setETitle(p.existing_title)
        if (p.new_title)                            setNTitle(p.new_title)
        if (p.book_title)                           setBTitle(p.book_title)
        if (p.chapter_number)                       setChNum(p.chapter_number)
      }
    }).catch(() => {})
  }, [])

  const submit = useCallback(async (decision) => {
    setSubmitting(true)
    try {
      await api.resolveChapterConflict({ decision })
      onDone()
    } catch (e) {
      console.error('Chapter conflict resolve failed:', e)
    } finally {
      setSubmitting(false)
    }
  }, [onDone])

  const joinLines = (lines) => Array.isArray(lines) ? lines.join('\n') : String(lines || '')

  return (
    <div className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-4">
      <div className="bg-slate-900 border border-slate-700 rounded-xl shadow-2xl w-full max-w-6xl max-h-[90vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center gap-3 px-5 py-4 border-b border-slate-700 shrink-0">
          <AlertTriangle size={18} className="text-amber-400" />
          <h2 className="text-sm font-semibold text-slate-200 flex-1">
            Chapter Conflict
            {chNum != null && (
              <span className="text-slate-400 font-normal"> — Chapter {chNum} of "{bTitle || `Book ${bookId}`}"</span>
            )}
          </h2>
          <span className="badge-amber text-xs">Confirm before overwrite</span>
        </div>

        {/* Explanation */}
        <div className="px-5 py-3 border-b border-slate-700 bg-amber-900/20 text-xs text-amber-200 leading-relaxed shrink-0">
          A chapter with this number already exists, but the source text differs from the queue item.
          This may be a real retranslation — or an author's note that was misnumbered. Compare the two
          panes below and choose whether to overwrite the existing chapter, or skip the queue item.
        </div>

        {/* Body — split view */}
        <div className="flex-1 grid grid-cols-1 md:grid-cols-2 gap-0 min-h-0 overflow-hidden">
          {/* Existing */}
          <div className="flex flex-col border-r border-slate-700 min-h-0">
            <div className="px-4 py-2 border-b border-slate-800 bg-slate-900/60 shrink-0">
              <div className="text-xs uppercase tracking-wide text-slate-500">Existing chapter</div>
              <div className="text-sm text-slate-200 font-medium truncate" title={eTitle}>
                {eTitle || <span className="text-slate-500 italic">(no title)</span>}
              </div>
            </div>
            <pre className="flex-1 overflow-auto p-4 text-xs leading-relaxed text-slate-300 whitespace-pre-wrap font-mono bg-slate-950/40">
              {joinLines(existing)}
            </pre>
          </div>

          {/* Incoming */}
          <div className="flex flex-col min-h-0">
            <div className="px-4 py-2 border-b border-slate-800 bg-slate-900/60 shrink-0">
              <div className="text-xs uppercase tracking-wide text-slate-500">Queue item (incoming)</div>
              <div className="text-sm text-slate-200 font-medium truncate" title={nTitle}>
                {nTitle || <span className="text-slate-500 italic">(no title)</span>}
              </div>
            </div>
            <pre className="flex-1 overflow-auto p-4 text-xs leading-relaxed text-slate-300 whitespace-pre-wrap font-mono bg-slate-950/40">
              {joinLines(incoming)}
            </pre>
          </div>
        </div>

        {/* Actions */}
        <div className="flex items-center gap-3 px-5 py-4 border-t border-slate-700 shrink-0">
          <span className="text-xs text-slate-500 flex-1">
            "Skip" drops the queue item and moves on to the next.
            "Overwrite" translates the queue item and replaces the existing chapter.
          </span>
          <button
            className="btn-secondary flex items-center gap-1.5"
            onClick={() => submit('cancel')}
            disabled={submitting}
          >
            <X size={13} /> Skip queue item
          </button>
          <button
            className="btn-danger flex items-center gap-1.5"
            onClick={() => submit('proceed')}
            disabled={submitting}
          >
            <Check size={13} /> Overwrite existing
          </button>
        </div>
      </div>
    </div>
  )
}
