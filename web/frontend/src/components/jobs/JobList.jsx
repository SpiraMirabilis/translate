import { useQuery } from '@tanstack/react-query'

import { api } from '../../services/api'
import { useJobs } from '../../hooks/useJobs'
import JobCard from './JobCard'
import ErrorBoundary from '../ErrorBoundary'

/**
 * Every running translation, one card each. Used by both Dashboard and Queue
 * so the two pages can't drift on what "running" looks like.
 */
export default function JobList({ onCancel, onStopAuto, emptyText = null }) {
  const { active, maxConcurrent } = useJobs()
  const booksQuery = useQuery({
    queryKey: ['books', 'minimal'],
    queryFn: () => api.listBooksMinimal(),
  })

  const titleFor = (bookId) =>
    (booksQuery.data?.books || []).find((b) => b.id === bookId)?.title

  if (!active.length) {
    return emptyText ? <p className="text-sm text-slate-500">{emptyText}</p> : null
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 text-xs text-slate-500">
        <span>{active.length} of {maxConcurrent} translation slots in use</span>
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
    </div>
  )
}
