import { Link } from 'react-router-dom'
import { User } from 'lucide-react'
import ProtagonistBadge from '../ProtagonistBadge'
import EditorsChoiceBadge from '../EditorsChoiceBadge'
import TagChips from '../TagChips'
import { hasEditorsChoice } from '../../utils/specialTags'
import { tagsWithLanguage, isNewlyAdded, sortTags } from '../../lib/libraryFilter'
import { relativeTime } from '../../lib/relativeTime'
import BookCover from './BookCover'
import { STATUS_CHIP } from './theme'

// One book in the grid or on a shelf: cover (badges + desktop hover pane with
// the blurb and tags), a 2px translation-progress bar, then three text rows —
// title, author, "Ch. N · 3h ago" (the last one links to the latest chapter).
export default function BookCard({ book, t, themeName, onTagClick, now = Date.now() }) {
  const tags = tagsWithLanguage(book)
  const editorsChoice = hasEditorsChoice(tags)
  const isNew = isNewlyAdded(book.created_date, now)
  const total = book.total_source_chapters > 0 ? book.total_source_chapters : null
  const pct = total ? Math.min(100, Math.round((book.chapter_count / total) * 100)) : null
  const status = book.status && book.status !== 'ongoing' ? book.status : null
  const when = relativeTime(book.last_chapter_date, now)

  return (
    <div className="group min-w-0">
      <Link to={`/library/book/${book.id}`} className="block">
        <BookCover book={book} t={t} themeName={themeName} ring={editorsChoice}>
          {isNew && (
            <div className="absolute top-1.5 left-1.5 z-10">
              <span className="px-1.5 py-0.5 rounded text-[10px] font-bold uppercase tracking-wide bg-amber-400 text-amber-950 shadow-sm">New</span>
            </div>
          )}
          {tags.length > 0 && (
            <div className="absolute top-1.5 right-1.5 z-10">
              <ProtagonistBadge tags={tags} size="sm" showLabel={false} overlay />
            </div>
          )}
          {editorsChoice && (
            <div className="absolute bottom-2 left-1/2 -translate-x-1/2 z-10 group-hover:opacity-0 transition-opacity">
              <EditorsChoiceBadge />
            </div>
          )}
          {/* Desktop hover pane: blurb + tags. Pointer-only, so touch users keep a plain tap-through. */}
          <div className="absolute inset-0 hidden md:flex flex-col justify-end p-2.5 bg-gradient-to-t from-black/90 via-black/55 to-black/0 opacity-0 group-hover:opacity-100 transition-opacity duration-200">
            {book.description && (
              <p className="text-[11px] leading-snug text-white/90 line-clamp-5 mb-2">{book.description}</p>
            )}
            {tags.length > 0 && (
              <TagChips tags={sortTags(tags)} size="sm" theme="dark" onTagClick={onTagClick} />
            )}
          </div>
        </BookCover>
        {pct != null && (
          <div className={`mt-1.5 h-0.5 rounded-full ${t.progressTrack} overflow-hidden`}
               title={`${book.chapter_count} of ${total} chapters translated (${pct}%)`}>
            <div className={`h-full ${t.progressFill}`} style={{ width: `${pct}%` }} />
          </div>
        )}
        <h3 className={`mt-2 font-semibold ${t.title} text-sm leading-tight line-clamp-2 ${t.titleHover} transition-colors`}>
          {book.title}
        </h3>
        {book.author && (
          <p className={`flex items-center gap-1 mt-0.5 text-xs ${t.author} truncate`}>
            <User size={11} className="shrink-0" />
            <span className="truncate">{book.author}</span>
          </p>
        )}
      </Link>
      {/* Own link, outside the card's <Link> (anchors can't nest). */}
      <p className={`mt-0.5 text-xs ${t.chapters} flex items-center gap-1.5 min-w-0`}>
        {book.latest_chapter != null ? (
          <Link
            to={`/library/read/${book.id}/${book.latest_chapter}`}
            title={`Latest: Chapter ${book.latest_chapter}${book.latest_chapter_title ? `: ${book.latest_chapter_title}` : ''}`}
            className="hover:text-indigo-500 transition-colors truncate"
          >
            Ch. {book.latest_chapter}{when && !status && <> · {when}</>}
          </Link>
        ) : (
          <span>{book.chapter_count} ch.</span>
        )}
        {status && (
          <span className={`shrink-0 px-1.5 py-0 rounded text-[10px] font-medium ${STATUS_CHIP[status] || 'bg-slate-500/20 text-slate-400'}`}>
            {status === 'ongoing-trial' ? 'trial' : status}
          </span>
        )}
      </p>
    </div>
  )
}
