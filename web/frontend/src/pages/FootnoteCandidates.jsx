import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { Asterisk, Loader2, ChevronRight } from 'lucide-react'
import { api } from '../services/api'
import { useSite } from '../App'

// Global footnote-candidate overview: every book with collected suggestions
// and its review-state counts. Per-book review happens on /footnotes/:bookId.
export default function FootnoteCandidates() {
  const { site_name } = useSite()
  const { data, isPending, error } = useQuery({
    queryKey: ['footnote-candidate-books'],
    queryFn: () => api.listFootnoteCandidateBooks(),
  })
  const books = data?.books || []

  useEffect(() => {
    document.title = `Footnotes | ${site_name}`
    return () => { document.title = site_name }
  }, [site_name])

  return (
    <div className="max-w-4xl mx-auto">
      <div className="flex items-center gap-2 mb-1">
        <Asterisk size={20} className="text-indigo-400" />
        <h1 className="text-xl font-semibold">Footnote candidates</h1>
      </div>
      <p className="text-sm text-slate-400 mb-6">
        Cultural-referent suggestions collected by the scanner (CLI bulk scans, or the
        Footnote Candidate Scanner module on chapter ingest). Review keeps or rejects
        suggestions — nothing is ever placed in a chapter from here.
      </p>

      {isPending && (
        <div className="flex justify-center py-16"><Loader2 size={24} className="animate-spin text-indigo-400" /></div>
      )}
      {error && <div className="text-rose-400 text-sm">Failed to load: {error.message}</div>}
      {!isPending && !error && books.length === 0 && (
        <div className="text-slate-400 text-sm border border-slate-700 rounded-lg p-6 text-center">
          No candidates collected yet. Run <code className="text-slate-300">footnote_scan.py -b &lt;book&gt;</code> or
          enable the Footnote Candidate Scanner module on a book.
        </div>
      )}

      <div className="flex flex-col gap-2">
        {books.map(b => (
          <Link
            key={b.book_id}
            to={`/footnotes/${b.book_id}`}
            className="flex items-center gap-4 bg-slate-800 border border-slate-700 rounded-lg px-4 py-3 hover:border-indigo-500/60 transition-colors"
          >
            <div className="flex-1 min-w-0">
              <div className="font-medium truncate">{b.book_title || `Book ${b.book_id}`}</div>
              <div className="text-xs text-slate-400 mt-0.5">
                {b.chapters_scanned} chapter{b.chapters_scanned === 1 ? '' : 's'} scanned
                {b.last_scanned_at && <> · last scan {String(b.last_scanned_at).slice(0, 10)}</>}
              </div>
            </div>
            <div className="flex items-center gap-2 text-xs shrink-0">
              {b.pending > 0 && (
                <span className="px-2 py-0.5 rounded-full bg-blue-500/20 text-blue-400">{b.pending} pending</span>
              )}
              <span className="px-2 py-0.5 rounded-full bg-emerald-500/20 text-emerald-400">{b.accepted} kept</span>
              <span className="px-2 py-0.5 rounded-full bg-slate-500/20 text-slate-400">{b.rejected} rejected</span>
            </div>
            <ChevronRight size={16} className="text-slate-500 shrink-0" />
          </Link>
        ))}
      </div>
    </div>
  )
}
