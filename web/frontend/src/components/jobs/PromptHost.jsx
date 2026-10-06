import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'

import { api } from '../../services/api'
import { useJobs } from '../../hooks/useJobs'
import EntityReviewPanel from '../EntityReviewPanel'
import JsonFixPanel from '../JsonFixPanel'
import ChapterConflictPanel from '../ChapterConflictPanel'

/**
 * Hosts the interactive translation prompts for every running book.
 *
 * Mounted once in Layout, so a prompt reaches the user wherever they are —
 * previously Queue force-navigated to "/" to show them.
 *
 * One modal at a time, queued rather than stacked: all three panels are
 * full-screen overlays with their own backdrop, so two at once would
 * double-dim the page and make Escape/Enter ambiguous. Per book the worker
 * blocks on exactly one prompt, so only cross-book simultaneity exists — and
 * a waiting book stays blocked harmlessly while you answer another.
 */
export default function PromptHost() {
  const { prompts, refresh, resolvePrompt } = useJobs()
  const [index, setIndex] = useState(0)

  const booksQuery = useQuery({
    queryKey: ['books', 'minimal'],
    queryFn: () => api.listBooksMinimal(),
    enabled: prompts.length > 0,
  })

  // Keep the cursor inside the list as prompts resolve.
  useEffect(() => {
    if (index >= prompts.length) setIndex(0)
  }, [prompts.length, index])

  if (!prompts.length) return null

  const job = prompts[Math.min(index, prompts.length - 1)]
  const bookTitle = (booksQuery.data?.books || []).find((b) => b.id === job.book_id)?.title
  const label = bookTitle || (job.book_id ? `Book ${job.book_id}` : 'Pasted text')
  // Close on the answer itself, then confirm against the server. The panels
  // answer over REST, so nothing on the socket announces the resolution — and
  // waiting for a snapshot to say so left the modal up (or, worse, flickering)
  // whenever a progress tick landed first.
  const done = () => {
    resolvePrompt(job.book_id, job.seq)
    refresh()
  }

  return (
    <>
      {prompts.length > 1 && (
        <div className="fixed top-3 left-1/2 -translate-x-1/2 z-[60] flex items-center gap-2
                        px-3 py-1.5 rounded-full bg-slate-800 border border-slate-700 shadow-lg">
          <button
            className="text-slate-400 hover:text-slate-200 px-1"
            onClick={() => setIndex((i) => (i - 1 + prompts.length) % prompts.length)}
            title="Previous decision"
          >‹</button>
          <span className="text-xs text-slate-300">
            Decision {Math.min(index, prompts.length - 1) + 1} of {prompts.length} · {label}
          </span>
          <button
            className="text-slate-400 hover:text-slate-200 px-1"
            onClick={() => setIndex((i) => (i + 1) % prompts.length)}
            title="Next decision"
          >›</button>
        </div>
      )}

      {job.status === 'awaiting_review' && job.pending_review && (
        <EntityReviewPanel
          key={`review-${job.book_id}`}
          bookId={job.book_id}
          entities={job.pending_review.entities}
          context={job.pending_review.context}
          phase={job.pending_review.phase}
          genderedCategories={job.pending_review.gendered_categories}
          noteUpdates={job.pending_review.note_updates}
          onDone={done}
        />
      )}

      {job.status === 'awaiting_json_fix' && job.pending_json_fix && (
        <JsonFixPanel
          key={`json-${job.book_id}`}
          bookId={job.book_id}
          rawResponse={job.pending_json_fix.raw_response}
          chunkIndex={job.pending_json_fix.chunk_index}
          totalChunks={job.pending_json_fix.total_chunks}
          chunkText={job.pending_json_fix.chunk_text}
          isEmpty={job.pending_json_fix.is_empty}
          timeoutSeconds={job.pending_json_fix.timeout_seconds}
          onDone={done}
        />
      )}

      {job.status === 'awaiting_chapter_conflict' && job.pending_chapter_conflict && (
        <ChapterConflictPanel
          key={`conflict-${job.book_id}`}
          bookId={job.pending_chapter_conflict.book_id ?? job.book_id}
          chapterNumber={job.pending_chapter_conflict.chapter_number}
          bookTitle={job.pending_chapter_conflict.book_title}
          existingTitle={job.pending_chapter_conflict.existing_title}
          existingUntranslated={job.pending_chapter_conflict.existing_untranslated}
          newTitle={job.pending_chapter_conflict.new_title}
          newUntranslated={job.pending_chapter_conflict.new_untranslated}
          errorMessage={job.pending_chapter_conflict.error}
          jev={job.pending_chapter_conflict.jev}
          onDone={done}
        />
      )}
    </>
  )
}
