import { useState, useEffect } from 'react'
import { X, Loader2, RotateCcw, ChevronLeft } from 'lucide-react'
import { api } from '../../services/api'
import { splitSegments, renderBlock } from '../../lib/chapterMarkdown'

function fmtWhen(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString()
}

/**
 * Slide-over revision history: list of manual/auto snapshots with a rendered
 * read-only preview and one-click restore (server snapshots current content
 * first, so restore itself is always undoable).
 */
export default function RevisionsPanel({ bookId, chapterNum, currentWords,
  illustrationUrls, onRestored, onClose }) {
  const [revisions, setRevisions] = useState(null)
  const [error, setError] = useState(null)
  const [selected, setSelected] = useState(null) // full revision payload
  const [loadingRev, setLoadingRev] = useState(false)
  const [restoring, setRestoring] = useState(false)

  const loadList = () => {
    api.listRevisions(bookId, chapterNum)
      .then((d) => setRevisions(d.revisions || []))
      .catch((e) => setError(e.message))
  }
  useEffect(loadList, [bookId, chapterNum]) // eslint-disable-line react-hooks/exhaustive-deps

  const openRevision = (rev) => {
    setLoadingRev(true)
    setError(null)
    api.getRevision(bookId, chapterNum, rev.id)
      .then(setSelected)
      .catch((e) => setError(e.message))
      .finally(() => setLoadingRev(false))
  }

  const handleRestore = async () => {
    if (!selected) return
    if (!window.confirm(`Restore the revision from ${fmtWhen(selected.created_at)}? Current content is snapshotted first.`)) return
    setRestoring(true)
    setError(null)
    try {
      const res = await api.restoreRevision(bookId, chapterNum, selected.id)
      onRestored(selected, res.translation_date)
      setSelected(null)
      loadList()
    } catch (e) {
      setError(e.message)
    } finally {
      setRestoring(false)
    }
  }

  return (
    <div className="fixed inset-y-0 right-0 z-40 w-full sm:w-[28rem] bg-slate-900 border-l border-slate-700 shadow-2xl flex flex-col">
      <div className="flex items-center justify-between px-4 py-3 border-b border-slate-700/60 shrink-0">
        <div className="flex items-center gap-2">
          {selected && (
            <button className="btn-ghost p-1" onClick={() => setSelected(null)} title="Back to list">
              <ChevronLeft size={15} />
            </button>
          )}
          <h2 className="font-semibold text-slate-200 text-sm">
            {selected ? fmtWhen(selected.created_at) : 'Revision history'}
          </h2>
        </div>
        <button className="btn-ghost p-1" onClick={onClose}><X size={15} /></button>
      </div>

      {error && <p className="px-4 py-2 text-xs text-rose-400">{error}</p>}

      {!selected ? (
        <div className="flex-1 overflow-y-auto">
          {revisions === null ? (
            <div className="flex justify-center py-10"><Loader2 size={18} className="animate-spin text-slate-500" /></div>
          ) : revisions.length === 0 ? (
            <p className="px-4 py-6 text-sm text-slate-500">
              No revisions yet — every explicit save (Ctrl+S) records one.
            </p>
          ) : (
            <ul>
              {revisions.map((rev) => (
                <li key={rev.id}>
                  <button
                    type="button"
                    className="w-full text-left px-4 py-2.5 border-b border-slate-800 hover:bg-slate-800/60"
                    onClick={() => openRevision(rev)}
                  >
                    <div className="flex items-center gap-2">
                      <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${
                        rev.kind === 'manual' ? 'bg-indigo-500/20 text-indigo-300' : 'bg-slate-700 text-slate-400'
                      }`}>
                        {rev.kind}
                      </span>
                      <span className="text-sm text-slate-300">{fmtWhen(rev.created_at)}</span>
                    </div>
                    <div className="mt-0.5 text-xs text-slate-500 tabular-nums">
                      {rev.title ? `${rev.title} · ` : ''}
                      {(rev.word_count ?? 0).toLocaleString()} words
                      {currentWords != null && rev.word_count != null && (
                        <span className={rev.word_count > currentWords ? 'text-emerald-500' : rev.word_count < currentWords ? 'text-rose-500/80' : ''}>
                          {' '}({rev.word_count - currentWords >= 0 ? '+' : ''}{(rev.word_count - currentWords).toLocaleString()} vs current)
                        </span>
                      )}
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          )}
          {loadingRev && (
            <div className="flex justify-center py-4"><Loader2 size={16} className="animate-spin text-slate-500" /></div>
          )}
        </div>
      ) : (
        <>
          <div className="flex-1 overflow-y-auto px-5 py-4 text-slate-300 text-sm leading-relaxed">
            {splitSegments(selected.content || []).map((seg, i) =>
              seg.type === 'img' ? (
                <div key={i} className="my-3 flex justify-center">
                  <img
                    src={illustrationUrls?.[seg.id] || `/api/books/${bookId}/illustration/${seg.id}`}
                    alt="" className="max-h-48 rounded"
                  />
                </div>
              ) : (
                <div key={i} className="chapter-markdown"
                  dangerouslySetInnerHTML={{ __html: renderBlock(seg.md) }} />
              ))}
          </div>
          <div className="px-4 py-3 border-t border-slate-700/60 flex justify-end gap-2 shrink-0">
            <button className="btn-secondary text-sm" onClick={() => setSelected(null)}>Back</button>
            <button className="btn-primary flex items-center gap-1.5 text-sm" onClick={handleRestore} disabled={restoring}>
              {restoring ? <Loader2 size={13} className="animate-spin" /> : <RotateCcw size={13} />}
              Restore this version
            </button>
          </div>
        </>
      )}
    </div>
  )
}
