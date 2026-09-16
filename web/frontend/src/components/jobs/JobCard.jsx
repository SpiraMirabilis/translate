import { Square } from 'lucide-react'

import TranslationProgress from '../TranslationProgress'
import StatusBadge from './StatusBadge'

/**
 * One running book. TranslationProgress is reused unchanged — it was already a
 * pure {progress, status} component, so it just needed to stop being fed a
 * single global status.
 */
export default function JobCard({ job, bookTitle, onCancel, onStopAuto }) {
  const chapter = job.chapter_number
  const title = bookTitle || (job.book_id ? `Book ${job.book_id}` : 'Pasted text')

  return (
    <div className="border border-slate-800 rounded-lg bg-slate-900/60 overflow-hidden">
      <div className="flex items-center gap-2 px-3 py-2 border-b border-slate-800">
        <span className="text-sm text-slate-200 font-medium truncate" title={title}>
          {title}
        </span>
        {chapter != null && (
          <span className="text-xs text-slate-500 shrink-0">Ch {chapter}</span>
        )}
        <div className="ml-auto flex items-center gap-2 shrink-0">
          <StatusBadge status={job.status} />
          {job.auto_process && !job.auto_process_stopping && onStopAuto && (
            <button
              className="btn-secondary text-xs px-2 py-1"
              onClick={() => onStopAuto(job.book_id)}
              title="Stop auto-processing after the current chapter"
            >
              Stop after current
            </button>
          )}
          {onCancel && (
            <button
              className="btn-danger text-xs px-2 py-1 flex items-center gap-1"
              onClick={() => onCancel(job.book_id)}
              title="Cancel this book's translation"
            >
              <Square size={11} /> Cancel
            </button>
          )}
        </div>
      </div>

      <div className="px-3 py-2">
        <TranslationProgress progress={job.progress} status={job.status} />
        {job.error && (
          <p className="text-xs text-rose-400 mt-1">{job.error}</p>
        )}
      </div>
    </div>
  )
}
