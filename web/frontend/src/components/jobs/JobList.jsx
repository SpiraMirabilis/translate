import { useQuery } from '@tanstack/react-query'
import { ArrowUpToLine, Clock, X } from 'lucide-react'

import { api } from '../../services/api'
import { useJobs } from '../../hooks/useJobs'
import JobCard from './JobCard'
import ErrorBoundary from '../ErrorBoundary'

/**
 * Every running translation, one card each, then the starts waiting for a
 * free slot. Used by both Dashboard and Queue so the two pages can't drift on
 * what "running" looks like.
 */
export default function JobList({ onCancel, onStopAuto, emptyText = null }) {
  const { active, pending = [], maxConcurrent, refresh } = useJobs()
  const booksQuery = useQuery({
    queryKey: ['books', 'minimal'],
    queryFn: () => api.listBooksMinimal(),
  })

  const titleFor = (bookId) =>
    (booksQuery.data?.books || []).find((b) => b.id === bookId)?.title

  if (!active.length && !pending.length) {
    return emptyText ? <p className="text-sm text-slate-500">{emptyText}</p> : null
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 text-xs text-slate-500">
        <span>{active.length} of {maxConcurrent} translation slots in use</span>
        {pending.length > 0 && <span>· {pending.length} waiting</span>}
      </div>
      {active.map((job) => (
        <ErrorBoundary key={job.book_id ?? 'nobook'} label={`Job card (book ${job.book_id ?? '—'})`}>
          <JobCard
            job={job}
            bookTitle={titleFor(job.book_id)}
            onCancel={onCancel}
            onStopAuto={onStopAuto}
          />
        </ErrorBoundary>
      ))}
      {pending.length > 0 && (
        <WaitingList pending={pending} titleFor={titleFor} onChange={refresh} />
      )}
    </div>
  )
}

/** Starts queued for a slot, in the order they will run. */
function WaitingList({ pending, titleFor, onChange }) {
  const act = async (fn) => {
    try { await fn() } catch { /* the next snapshot shows the truth */ }
    onChange()
  }

  return (
    <div className="border border-slate-800 rounded-lg bg-slate-900/40 divide-y divide-slate-800">
      {pending.map((p) => {
        const title = p.book_title || titleFor(p.book_id)
          || (p.book_id ? `Book ${p.book_id}` : p.kind === 'text' ? 'Pasted text' : 'Next queued book')
        const what = p.kind === 'text'
          ? `Translate${p.chapter_number != null ? ` ch ${p.chapter_number}` : ''}`
          : p.auto_process
            ? `Auto-process${p.max_chapters ? ` (${p.max_chapters} ch)` : ''}`
            : 'Next chapter'
        return (
          <div key={p.id} className="flex items-center gap-2 px-3 py-1.5 text-xs">
            <Clock size={12} className="text-slate-500 shrink-0" />
            <span className="text-slate-500 w-5 shrink-0 text-right">#{p.position}</span>
            <span className="text-slate-300 truncate" title={title}>{title}</span>
            <span className="text-slate-500 shrink-0">{what}</span>
            {p.priority && (
              <span className="px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-300 shrink-0">priority</span>
            )}
            {p.source === 'scrape' && (
              <span className="px-1.5 py-0.5 rounded bg-sky-500/15 text-sky-300 shrink-0"
                    title="Started by the scrape worker's auto-translate">auto</span>
            )}
            {p.translation_model && (
              <span className="text-slate-600 font-mono truncate hidden sm:inline" title={p.translation_model}>
                {p.translation_model}
              </span>
            )}
            <div className="ml-auto flex items-center gap-1 shrink-0">
              {!p.priority && (
                <button
                  className="btn-secondary text-xs px-1.5 py-0.5 flex items-center"
                  onClick={() => act(() => api.setPendingPriority(p.id, true))}
                  title="Priority: run this at the next free slot"
                >
                  <ArrowUpToLine size={11} />
                </button>
              )}
              <button
                className="btn-secondary text-xs px-1.5 py-0.5 flex items-center"
                onClick={() => act(() => api.removePendingStart(p.id))}
                title="Remove from the waiting list"
              >
                <X size={11} />
              </button>
            </div>
          </div>
        )
      })}
    </div>
  )
}
