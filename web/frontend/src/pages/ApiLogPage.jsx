import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../services/api'
import BookPicker from '../components/BookPicker'
import ApiLogSessions from '../components/apilog/ApiLogSessions'
import { Filter } from 'lucide-react'

export default function ApiLogPage() {
  const [bookFilter, setBookFilter] = useState('')
  const booksQuery = useQuery({
    queryKey: ['books', 'minimal'],
    queryFn: () => api.listBooksMinimal(),
  })
  const books = booksQuery.data?.books || []

  return (
    <div className="max-w-6xl mx-auto p-4 md:p-6">
      <div className="mb-6">
        <h1 className="text-lg font-semibold text-slate-100">API Logs</h1>
      </div>

      {books.length > 0 && (
        <div className="flex items-center gap-2 mb-4">
          <Filter size={12} className="text-slate-500" />
          <BookPicker
            className="w-72"
            inputClassName="input text-xs py-1 px-2"
            books={books}
            value={bookFilter}
            onChange={setBookFilter}
            extraOptions={[{ value: '', label: 'All books' }]}
          />
        </div>
      )}

      <ApiLogSessions bookId={bookFilter ? Number(bookFilter) : null} showBook />
    </div>
  )
}
