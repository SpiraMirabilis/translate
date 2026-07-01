/**
 * ChapterConflictPanel
 *
 * Modal that appears before translating a queue item whose chapter_number
 * collides with an already-translated chapter, and whose source text differs.
 * Shows the existing chapter and the incoming queue item side-by-side and
 * asks the user how to resolve: skip, merge, overwrite, renumber the
 * existing chapter, or renumber the incoming chapter.
 */
import { useState, useCallback, useEffect } from 'react'
import { api } from '../services/api'
import { AlertTriangle, Check, X, Merge, ArrowRightLeft } from 'lucide-react'

export default function ChapterConflictPanel({
  bookId,
  chapterNumber,
  bookTitle,
  existingTitle,
  existingUntranslated,
  newTitle,
  newUntranslated,
  errorMessage,
  onDone,
}) {
  const [submitting, setSubmitting] = useState(false)
  const [renumberMode, setRenumberMode] = useState(null) // null | 'existing' | 'new'
  const [renumberDraft, setRenumberDraft] = useState('')
  const [errorMsg, setErrorMsg] = useState(null)
  // Which action button is currently hovered, to show the directional arrow
  // overlay across the split view. null | 'overwrite' | 'skip'.
  const [hoverHint, setHoverHint] = useState(null)

  // Re-fetch the payload from the API on mount as a safety net — mirrors
  // the JsonFixPanel pattern so the modal still works after a page refresh.
  const [existing, setExisting] = useState(existingUntranslated || [])
  const [incoming, setIncoming] = useState(newUntranslated || [])
  const [eTitle, setETitle] = useState(existingTitle || '')
  const [nTitle, setNTitle] = useState(newTitle || '')
  const [bTitle, setBTitle] = useState(bookTitle || '')
  const [chNum, setChNum] = useState(chapterNumber)

  // Sync props to local state — Dashboard updates props in place when a
  // cascading conflict_needed websocket arrives, so we follow the new payload.
  useEffect(() => {
    setExisting(existingUntranslated || [])
    setIncoming(newUntranslated || [])
    setETitle(existingTitle || '')
    setNTitle(newTitle || '')
    setBTitle(bookTitle || '')
    setChNum(chapterNumber)
    setRenumberMode(null)
    setRenumberDraft('')
    setErrorMsg(errorMessage || null)
  }, [existingUntranslated, newUntranslated, existingTitle, newTitle, bookTitle, chapterNumber, errorMessage])

  // On mount, fetch the latest payload as a safety net for the case where
  // the user refreshed mid-conflict and the props are stale.
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
        if (p.error)                                setErrorMsg(p.error)
      }
    }).catch(() => {})
  }, [])

  const submit = useCallback(async (decision, newChapterNumber = null) => {
    setSubmitting(true)
    setErrorMsg(null)
    try {
      await api.resolveChapterConflict({
        decision,
        new_chapter_number: newChapterNumber,
      })
      // Cascade: if the renumber surfaces a new conflict, the backend will
      // emit another `chapter_conflict_needed` message and Dashboard will
      // re-open the panel with fresh data. Closing here is safe.
      onDone()
    } catch (e) {
      console.error('Chapter conflict resolve failed:', e)
      setErrorMsg(e.message || 'Failed to resolve conflict.')
      setSubmitting(false)
    }
  }, [onDone])

  const submitRenumber = () => {
    const n = parseInt(renumberDraft, 10)
    if (!Number.isFinite(n) || n < 1) {
      setErrorMsg('Chapter number must be a positive integer.')
      return
    }
    if (n === chNum) {
      setErrorMsg('Pick a different chapter number.')
      return
    }
    submit(renumberMode === 'existing' ? 'renumber_existing' : 'renumber_new', n)
  }

  const cancelRenumber = () => {
    setRenumberMode(null)
    setRenumberDraft('')
    setErrorMsg(null)
  }

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
              <span className="text-slate-400 font-normal"> — Chapter {chNum} of &quot;{bTitle || `Book ${bookId}`}&quot;</span>
            )}
          </h2>
          <span className="badge-amber text-xs">Confirm before overwrite</span>
        </div>

        {/* Explanation */}
        <div className="px-5 py-3 border-b border-slate-700 bg-amber-900/20 text-xs text-amber-200 leading-relaxed shrink-0">
          A chapter with this number already exists, but the source text differs from the queue item.
          Compare the two panes below and choose how to resolve: skip, merge, overwrite, or renumber
          either chapter so they no longer collide.
        </div>

        {/* Body — split view */}
        <div className="relative flex-1 grid grid-cols-1 md:grid-cols-2 gap-0 min-h-0 overflow-hidden">
          {/* Directional hint arrow — shown while hovering Skip / Overwrite.
              Overwrite: arrow points left (incoming, right pane, wins).
              Skip: arrow points right (existing, left pane, wins). */}
          {hoverHint && (
            <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center">
              <ConflictArrow direction={hoverHint === 'overwrite' ? 'left' : 'right'} />
            </div>
          )}
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

        {/* Error banner */}
        {errorMsg && (
          <div className="px-5 py-2 border-t border-rose-700/50 bg-rose-900/30 text-xs text-rose-200 shrink-0">
            {errorMsg}
          </div>
        )}

        {/* Actions */}
        {renumberMode ? (
          <div className="flex items-center gap-3 px-5 py-4 border-t border-slate-700 shrink-0 flex-wrap">
            <span className="text-xs text-slate-300">
              {renumberMode === 'existing'
                ? `Move existing chapter ${chNum} to new number:`
                : `Translate incoming queue item as chapter:`}
            </span>
            <input
              autoFocus
              type="number"
              min="1"
              className="px-2 py-1 bg-slate-800 border border-slate-600 rounded text-sm text-slate-200 outline-none focus:border-sky-500 w-24"
              value={renumberDraft}
              onChange={e => { setRenumberDraft(e.target.value); setErrorMsg(null) }}
              onKeyDown={e => {
                if (e.key === 'Enter') { e.preventDefault(); submitRenumber() }
                else if (e.key === 'Escape') { cancelRenumber() }
              }}
              disabled={submitting}
            />
            <div className="flex-1" />
            <button
              className="btn-secondary flex items-center gap-1.5"
              onClick={cancelRenumber}
              disabled={submitting}
            >
              <X size={13} /> Cancel
            </button>
            <button
              className="btn-primary flex items-center gap-1.5"
              onClick={submitRenumber}
              disabled={submitting}
            >
              <Check size={13} /> Confirm renumber
            </button>
          </div>
        ) : (
          <div className="px-5 py-4 border-t border-slate-700 shrink-0">
            <div className="text-xs text-slate-500 mb-3">
              &quot;Skip&quot; drops the queue item. &quot;Append &amp; translate new part&quot; keeps the existing
              translation and only translates the appended source, joining the two.
              &quot;Renumber&quot; moves either chapter to a new number. &quot;Insert &amp; shift queue&quot; translates
              this item as the next chapter and bumps every later queue item up by one.
              &quot;Overwrite&quot; replaces the existing chapter.
            </div>
            <div className="flex items-center gap-2 flex-wrap justify-end">
            <button
              className="btn-success flex items-center gap-1.5"
              onClick={() => submit('cancel')}
              onMouseEnter={() => setHoverHint('skip')}
              onMouseLeave={() => setHoverHint(null)}
              onFocus={() => setHoverHint('skip')}
              onBlur={() => setHoverHint(null)}
              disabled={submitting}
            >
              <X size={13} /> Skip queue item
            </button>
            <button
              className="btn-secondary flex items-center gap-1.5"
              onClick={() => submit('merge')}
              disabled={submitting}
            >
              <Merge size={13} /> Append &amp; translate new part
            </button>
            <button
              className="btn-secondary flex items-center gap-1.5"
              onClick={() => { setRenumberMode('existing'); setRenumberDraft(''); setErrorMsg(null) }}
              disabled={submitting}
              title="Move the existing chapter to a different number, then translate the queue item at this number"
            >
              <ArrowRightLeft size={13} /> Renumber existing
            </button>
            <button
              className="btn-secondary flex items-center gap-1.5"
              onClick={() => { setRenumberMode('new'); setRenumberDraft(''); setErrorMsg(null) }}
              disabled={submitting}
              title="Translate the queue item as a different chapter number, leaving the existing chapter alone"
            >
              <ArrowRightLeft size={13} /> Renumber new
            </button>
            <button
              className="btn-secondary flex items-center gap-1.5"
              onClick={() => submit('insert_shift')}
              disabled={submitting}
              title="Translate this item as the next chapter (N+1) and shift every later queue item up by one"
            >
              <ArrowRightLeft size={13} /> Insert &amp; shift queue
            </button>
            <button
              className="btn-danger flex items-center gap-1.5"
              onClick={() => submit('proceed')}
              onMouseEnter={() => setHoverHint('overwrite')}
              onMouseLeave={() => setHoverHint(null)}
              onFocus={() => setHoverHint('overwrite')}
              onBlur={() => setHoverHint(null)}
              disabled={submitting}
            >
              <Check size={13} /> Overwrite existing
            </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

/**
 * ConflictArrow — thick directional arrow drawn across the split view to
 * hint which pane "wins" for the hovered action.
 *   direction 'left'  → overwrite: incoming (right pane) replaces existing.
 *   direction 'right' → skip: existing (left pane) is kept.
 */
function ConflictArrow({ direction }) {
  const isLeft = direction === 'left'
  const color = isLeft ? '#f43f5e' : '#34d399' // rose-500 / emerald-400
  const label = isLeft ? 'Incoming overwrites existing' : 'Existing chapter is kept'
  return (
    <div className="flex flex-col items-center gap-3">
      <svg
        viewBox="0 0 300 120"
        className={`w-[58%] max-w-[460px] ${isLeft ? 'conflict-arrow-left' : 'conflict-arrow-right'}`}
        style={{ filter: `drop-shadow(0 0 12px ${color})` }}
      >
        <polygon
          points="8,38 178,38 178,8 292,60 178,112 178,82 8,82"
          fill={color}
          stroke="#0f172a"
          strokeWidth="5"
          strokeLinejoin="round"
          transform={isLeft ? 'translate(300,0) scale(-1,1)' : undefined}
        />
      </svg>
      <span
        className="text-xs font-semibold px-2.5 py-1 rounded border bg-slate-950/90"
        style={{ color, borderColor: color }}
      >
        {label}
      </span>
    </div>
  )
}
