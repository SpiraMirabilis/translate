import { useQuery } from '@tanstack/react-query'
import { useParams, useSearchParams, Link } from 'react-router-dom'
import { api } from '../services/api'
import ApiLogSessions from '../components/apilog/ApiLogSessions'
import { ArrowLeft } from 'lucide-react'

export default function ApiCalls() {
  const { bookId } = useParams()
  const [searchParams, setSearchParams] = useSearchParams()
  const chapterFilter = searchParams.get('chapter') != null ? Number(searchParams.get('chapter')) : null

  const bookQuery = useQuery({
    queryKey: ['books', 'detail', bookId],
    queryFn: () => api.getBook(bookId),
  })
  const chaptersQuery = useQuery({
    queryKey: ['chapters', bookId],
    queryFn: () => api.listChapters(bookId),
  })

  const book = bookQuery.data ?? null
  // Build unique chapter numbers for the filter dropdown
  const chList = (chaptersQuery.data?.chapters || []).map(c => c.chapter_number)
  const chapters = [...new Set(chList)].sort((a, b) => a - b)

  return (
    <div className="max-w-6xl mx-auto p-4 md:p-6">
      {/* Header */}
      <div className="flex items-center gap-3 mb-6">
        <Link to="/books" className="text-slate-400 hover:text-slate-200">
          <ArrowLeft size={18} />
        </Link>
        <div>
          <h1 className="text-lg font-semibold text-slate-100">API Call Logs</h1>
          {book && <p className="text-sm text-slate-400">{book.title}</p>}
        </div>
      </div>

      {/* Chapter filter */}
      {chapters.length > 0 && (
        <div className="flex items-center gap-2 mb-4">
          <label className="text-xs text-slate-400">Filter by chapter:</label>
          <select
            className="input text-xs py-1 px-2 w-32"
            value={chapterFilter ?? ''}
            onChange={(e) => {
              const v = e.target.value
              if (v === '') {
                searchParams.delete('chapter')
              } else {
                searchParams.set('chapter', v)
              }
              setSearchParams(searchParams)
            }}
          >
            <option value="">All chapters</option>
            {chapters.map(n => (
              <option key={n} value={n}>Chapter {n}</option>
            ))}
          </select>
        </div>
      )}

      <ApiLogSessions
        bookId={Number(bookId)}
        chapterNumber={chapterFilter}
        emptyText="No API calls logged yet for this book."
      />
    </div>
  )
}
