import { Link } from 'react-router-dom'
import { Play } from 'lucide-react'
import BookCover from './BookCover'

// A book the reader has opened before: tap goes straight back into the chapter.
export default function ContinueReadingCard({ book, chapter, t, themeName }) {
  const latest = book.latest_chapter || book.chapter_count || chapter
  const pct = latest ? Math.min(100, Math.round((chapter / latest) * 100)) : 0
  const unread = latest > chapter ? latest - chapter : 0
  return (
    <Link to={`/library/read/${book.id}/${chapter}`} className="group block min-w-0">
      <BookCover book={book} t={t} themeName={themeName}>
        <div className="absolute inset-0 flex items-center justify-center bg-black/0 group-hover:bg-black/30 transition-colors">
          <span className="w-10 h-10 rounded-full bg-white/90 text-indigo-600 flex items-center justify-center shadow-lg opacity-0 group-hover:opacity-100 scale-90 group-hover:scale-100 transition-all">
            <Play size={18} fill="currentColor" className="ml-0.5" />
          </span>
        </div>
        {unread > 0 && (
          <div className="absolute top-1.5 right-1.5 z-10">
            <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-indigo-500 text-white shadow-sm">+{unread}</span>
          </div>
        )}
      </BookCover>
      <div className={`mt-1.5 h-0.5 rounded-full ${t.progressTrack} overflow-hidden`}>
        <div className={`h-full ${t.progressFill}`} style={{ width: `${pct}%` }} />
      </div>
      <h3 className={`mt-2 font-semibold ${t.title} text-sm leading-tight line-clamp-2 ${t.titleHover} transition-colors`}>{book.title}</h3>
      <p className={`mt-0.5 text-xs ${t.chapters}`}>Ch. {chapter}{latest ? ` of ${latest}` : ''}</p>
    </Link>
  )
}
