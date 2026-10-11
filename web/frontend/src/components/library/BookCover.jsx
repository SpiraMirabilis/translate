import { BookOpen } from 'lucide-react'
import { bustUrl } from '../../services/cacheBust'
import { placeholderStyle } from './theme'

// The 2:3 cover block shared by every card. Children are overlaid (badges,
// hover panes); `ring` gilds an editor's-choice pick.
export default function BookCover({ book, t, themeName, ring = false, className = '', children }) {
  return (
    <div className={`aspect-[2/3] rounded-lg overflow-hidden ${t.cardBg} shadow-md group-hover:shadow-xl transition-shadow duration-300 relative ${ring ? 'ring-2 ring-amber-400 ring-offset-2 ring-offset-transparent shadow-amber-500/30' : ''} ${className}`}>
      {book.cover_image ? (
        <img
          src={bustUrl(book.cover_medium_url || `/api/public/books/${book.id}/cover/medium`)}
          alt={book.title}
          loading="lazy"
          className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
        />
      ) : (
        <div className="w-full h-full flex flex-col items-center justify-center p-4" style={placeholderStyle(book.id, themeName)}>
          <BookOpen size={32} className={`${t.placeholderIcon} mb-3`} />
          <span className={`text-sm font-medium ${t.placeholderText} text-center leading-tight line-clamp-4`}>
            {book.title}
          </span>
        </div>
      )}
      {children}
    </div>
  )
}
