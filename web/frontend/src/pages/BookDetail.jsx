import { useState, useEffect } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useParams, Link, useNavigate } from 'react-router-dom'
import { BookOpen, Loader2, ArrowLeft, Download, ChevronRight, Sun, Moon, Sunset, User, BookText, Rss, MessageCircle, X } from 'lucide-react'
import { useReaderPrefs } from '../hooks/useReaderPrefs'
import { useLocalStorage } from '../hooks/useLocalStorage'
import { useBookFeedLink } from '../hooks/useBookFeedLink'
import { useUrlModal } from '../hooks/useUrlState'
import { useSite } from '../App'
import { bustUrl } from '../services/cacheBust'
import { publicApi } from '../services/api'
import ReaderComments from '../components/ReaderComments'
import { loadIdentity } from '../components/CommentForm'
import TagChips from '../components/TagChips'
import ProtagonistBadge from '../components/ProtagonistBadge'

// Sentinel chapter_number for book-level discussions. Translated novels
// don't have a real "chapter 0" (prologues are conventionally Chapter 1
// with a Prologue title), so we overload it as the book-page target.
const BOOK_DISCUSSION_CH = 0

const THEME_TOGGLE = [
  { id: 'light', icon: Sun,    label: 'Light' },
  { id: 'sepia', icon: Sunset, label: 'Sepia' },
  { id: 'dark',  icon: Moon,   label: 'Dark'  },
]

const T = {
  light: {
    headerBg: 'bg-white', headerBorder: 'border-stone-200',
    subtitle: 'text-gray-500', cardBg: 'bg-stone-200',
    placeholderFrom: 'from-indigo-100', placeholderTo: 'to-indigo-50',
    placeholderIcon: 'text-indigo-300',
    title: 'text-gray-900', author: 'text-gray-500', meta: 'text-gray-400',
    toggleBg: 'bg-stone-100', toggleActive: 'bg-white text-indigo-600 shadow-sm',
    toggleInactive: 'text-gray-400 hover:text-gray-600',
    btnPrimary: 'bg-indigo-600 hover:bg-indigo-700 text-white',
    btnSecondary: 'bg-stone-200 hover:bg-stone-300 text-gray-700',
    divider: 'border-stone-200',
    chapterRow: 'hover:bg-stone-100',
    chapterNum: 'text-gray-400',
    chapterTitle: 'text-gray-700',
    progressHighlight: 'bg-indigo-50 border-l-2 border-indigo-400',
    description: 'text-gray-700',
    sectionTitle: 'text-gray-900',
    link: 'text-indigo-600 hover:text-indigo-700',
  },
  sepia: {
    headerBg: 'bg-amber-100/80', headerBorder: 'border-amber-200',
    subtitle: 'text-amber-800/60', cardBg: 'bg-amber-200/50',
    placeholderFrom: 'from-amber-200/60', placeholderTo: 'to-amber-100/60',
    placeholderIcon: 'text-amber-400',
    title: 'text-amber-950', author: 'text-amber-800/60', meta: 'text-amber-700/50',
    toggleBg: 'bg-amber-200/50', toggleActive: 'bg-amber-50 text-amber-900 shadow-sm',
    toggleInactive: 'text-amber-700/50 hover:text-amber-900',
    btnPrimary: 'bg-indigo-600 hover:bg-indigo-700 text-white',
    btnSecondary: 'bg-amber-200/60 hover:bg-amber-200 text-amber-900',
    divider: 'border-amber-200',
    chapterRow: 'hover:bg-amber-100/50',
    chapterNum: 'text-amber-700/50',
    chapterTitle: 'text-amber-900',
    progressHighlight: 'bg-amber-100 border-l-2 border-indigo-400',
    description: 'text-amber-900',
    sectionTitle: 'text-amber-950',
    link: 'text-indigo-700 hover:text-indigo-800',
  },
  dark: {
    headerBg: 'bg-slate-800/80', headerBorder: 'border-slate-700',
    subtitle: 'text-slate-400', cardBg: 'bg-slate-800',
    placeholderFrom: 'from-slate-800', placeholderTo: 'to-slate-700',
    placeholderIcon: 'text-slate-500',
    title: 'text-slate-100', author: 'text-slate-400', meta: 'text-slate-500',
    toggleBg: 'bg-slate-800', toggleActive: 'bg-slate-700 text-slate-100 shadow-sm',
    toggleInactive: 'text-slate-500 hover:text-slate-300',
    btnPrimary: 'bg-indigo-600 hover:bg-indigo-500 text-white',
    btnSecondary: 'bg-slate-700 hover:bg-slate-600 text-slate-200',
    divider: 'border-slate-700',
    chapterRow: 'hover:bg-slate-800/50',
    chapterNum: 'text-slate-500',
    chapterTitle: 'text-slate-300',
    progressHighlight: 'bg-slate-800 border-l-2 border-indigo-400',
    description: 'text-slate-300',
    sectionTitle: 'text-slate-100',
    link: 'text-indigo-400 hover:text-indigo-300',
  },
}

const INITIAL_CHAPTERS = 50

export default function BookDetail() {
  const { bookId } = useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [showAll, setShowAll] = useState(false)
  const { prefs, setPrefs, theme } = useReaderPrefs()
  const [progress] = useLocalStorage('reader-progress', {})
  const { site_name, public_site_name, azw3_available } = useSite()
  const t = T[prefs.theme] || T.light

  const commentsModal = useUrlModal('comments')
  const commentsOpen = commentsModal.isOpen

  // Identity header lets the API include the caller's own pending comments.
  const commentCountQuery = useQuery({
    queryKey: ['public', 'comment-count', bookId, BOOK_DISCUSSION_CH],
    queryFn: () => publicApi.getChapterCommentCount(bookId, BOOK_DISCUSSION_CH, loadIdentity()?.uuid),
    enabled: !!bookId,
  })
  const commentCount = commentCountQuery.data?.count || 0
  const commentsEnabled = commentCountQuery.data ? commentCountQuery.data.enabled !== false : true
  const refreshCommentCount = () =>
    queryClient.invalidateQueries({ queryKey: ['public', 'comment-count', bookId, BOOK_DISCUSSION_CH] })

  const bookQuery = useQuery({
    queryKey: ['public', 'books', 'detail', bookId],
    queryFn: () => publicApi.getBook(bookId),
  })
  const chaptersQuery = useQuery({
    queryKey: ['public', 'books', 'detail', bookId, 'chapters'],
    queryFn: () => publicApi.listChapters(bookId),
  })
  const book = bookQuery.data ?? null
  const chapters = chaptersQuery.data?.chapters || []
  const loading = bookQuery.isPending || chaptersQuery.isPending
  const error = bookQuery.isError || chaptersQuery.isError

  useEffect(() => {
    if (book) document.title = `${book.title} — ${public_site_name}`
    return () => { document.title = site_name }
  }, [book, public_site_name, site_name])

  // Per-book RSS autodiscovery — no-op when the server already injected the tag
  useBookFeedLink(bookId, book?.title)

  const cycleTheme = (id) => setPrefs(p => ({ ...p, theme: id }))

  // Which ebook format is currently downloading ('epub' | 'azw3' | null).
  // AZW3 can take a while to convert on the first hit, so the button shows a spinner.
  const [downloading, setDownloading] = useState(null)
  // Notice shown when a Kindle (AZW3) file must be generated on demand.
  // { type: 'info' | 'error', msg } or null.
  const [kindleToast, setKindleToast] = useState(null)

  const handleEbookDownload = async (format) => {
    setDownloading(format)
    try {
      // For Kindle files, check first whether the artifact is already in the CDN.
      // If not, it's built on demand (can take a few minutes) — warn the reader.
      if (format === 'azw3') {
        try {
          const status = await publicApi.getAzw3Status(bookId)
          if (status && status.cached === false) {
            setKindleToast({
              type: 'info',
              msg: "Preparing your Kindle file — it's being generated on demand and can take a few minutes. "
                 + "It'll download automatically when it's ready. If it doesn't, check back in a few minutes.",
            })
          }
        } catch {
          // Status check failed — proceed with the download anyway.
        }
      }
      const res = await fetch(`/api/public/books/${bookId}/${format}`, { credentials: 'same-origin' })
      if (!res.ok) throw new Error('Download failed')
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      const disposition = res.headers.get('content-disposition') || ''
      const utf8Match = disposition.match(/filename\*=UTF-8''([^;]+)/i)
      const asciiMatch = disposition.match(/filename="([^"]+)"/i)
      const parsedName = utf8Match ? decodeURIComponent(utf8Match[1]) : asciiMatch?.[1]
      a.download = parsedName || `${book?.title || 'book'}.${format}`
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
      // The file is here — clear any "preparing" notice.
      if (format === 'azw3') setKindleToast(null)
    } catch {
      // Surface Kindle failures (it may have been a long wait); EPUB stays silent.
      if (format === 'azw3') {
        setKindleToast({
          type: 'error',
          msg: 'Sorry — preparing the Kindle file didn\'t work. Please try again in a few minutes.',
        })
      }
    } finally {
      setDownloading(null)
    }
  }

  const currentChapter = progress?.[bookId]
  const hasProgress = currentChapter && currentChapter > 1
  // First real chapter to open when there's no saved progress. Skips the
  // book-discussion sentinel and doesn't assume chapters start at 1.
  const firstChapter = chapters.find(ch => ch.chapter > BOOK_DISCUSSION_CH)?.chapter ?? 1

  const displayedChapters = showAll ? chapters : chapters.slice(0, INITIAL_CHAPTERS)

  if (loading) {
    return (
      <div className={`min-h-screen ${theme.bg} ${theme.text} flex items-center justify-center`}>
        <Loader2 size={32} className="animate-spin text-indigo-400" />
      </div>
    )
  }

  if (error || !book) {
    return (
      <div className={`min-h-screen ${theme.bg} ${theme.text} flex flex-col items-center justify-center gap-4`}>
        <BookText size={48} className="opacity-30" />
        <p className={`${t.subtitle} text-lg`}>Book not found</p>
        <Link to="/library" className={`${t.link} text-sm`}>Back to Library</Link>
      </div>
    )
  }

  return (
    <div className={`min-h-screen ${theme.bg} ${theme.text} transition-colors duration-300`}>
      {/* Header */}
      <header className={`${t.headerBg} border-b ${t.headerBorder} shadow-sm transition-colors duration-300`}>
        <div className="max-w-4xl mx-auto px-6 py-4 flex items-center justify-between">
          <Link to="/library" className={`flex items-center gap-1.5 text-sm ${t.link} transition-colors`}>
            <ArrowLeft size={16} />
            Library
          </Link>
          <Link to="/library" className="flex items-center gap-2">
            <BookOpen size={22} className="text-indigo-500" />
            <span className={`text-lg font-bold ${t.title} tracking-tight hidden sm:inline`}>{public_site_name}</span>
          </Link>
          <div className={`flex items-center gap-0.5 ${t.toggleBg} rounded-lg p-1 transition-colors duration-300`}>
            {THEME_TOGGLE.map(({ id, icon: Icon, label }) => (
              <button
                key={id}
                onClick={() => cycleTheme(id)}
                title={label}
                className={`p-1.5 rounded-md transition-all duration-200 ${prefs.theme === id ? t.toggleActive : t.toggleInactive}`}
              >
                <Icon size={14} />
              </button>
            ))}
          </div>
        </div>
      </header>

      {/* Content */}
      <main className="max-w-4xl mx-auto px-6 py-8">
        {/* Book info section */}
        <div className="flex flex-col sm:flex-row gap-6 sm:gap-8">
          {/* Cover */}
          <div className="w-48 sm:w-56 flex-shrink-0 mx-auto sm:mx-0">
            <div className={`aspect-[2/3] rounded-lg overflow-hidden ${t.cardBg} shadow-lg`}>
              {book.cover_image ? (
                <img
                  src={bustUrl(book.cover_medium_url || `/api/public/books/${bookId}/cover/medium`)}
                  alt={book.title}
                  className="w-full h-full object-cover"
                />
              ) : (
                <div className={`w-full h-full flex flex-col items-center justify-center bg-gradient-to-br ${t.placeholderFrom} ${t.placeholderTo} p-4`}>
                  <BookOpen size={48} className={`${t.placeholderIcon} mb-3`} />
                </div>
              )}
            </div>
          </div>

          {/* Metadata */}
          <div className="flex flex-col justify-center text-center sm:text-left">
            <div className="flex items-center gap-2 flex-wrap justify-center sm:justify-start">
              <h1 className={`text-2xl sm:text-3xl font-bold ${t.title} leading-tight`}>{book.title}</h1>
              <ProtagonistBadge tags={book.tags} size="md" theme={prefs.theme} />
            </div>
            {book.author && (
              <p className={`flex items-center gap-1.5 mt-2 ${t.author} justify-center sm:justify-start`}>
                <User size={14} />
                {book.author}
              </p>
            )}
            <p className={`mt-1 text-sm ${t.meta} flex items-center gap-1.5 flex-wrap justify-center sm:justify-start`}>
              <span>
                {chapters.length} chapter{chapters.length !== 1 ? 's' : ''}
                {book.total_source_chapters > 0 && (
                  <> / {book.total_source_chapters} ({Math.round((chapters.length / book.total_source_chapters) * 100)}%)</>
                )}
              </span>
              {book.source_language && <span>&middot; Source: {book.source_language}</span>}
              {book.status && book.status !== 'ongoing' && (
                <span className={`px-1.5 py-0 rounded text-[10px] font-medium ${
                  book.status === 'completed' ? 'bg-emerald-500/20 text-emerald-400' :
                  book.status === 'hiatus' ? 'bg-amber-500/20 text-amber-400' :
                  book.status === 'dropped' ? 'bg-rose-500/20 text-rose-400' :
                  book.status === 'ongoing-trial' ? 'bg-cyan-500/20 text-cyan-400' :
                  'bg-slate-500/20 text-slate-400'
                }`}>{book.status}</span>
              )}
            </p>
            {book.tags && book.tags.length > 0 && (
              <div className="mt-2 flex justify-center sm:justify-start">
                <TagChips
                  tags={book.tags}
                  size="md"
                  theme={prefs.theme}
                  onTagClick={(tag) => navigate(`/library?tag=${encodeURIComponent(tag)}`)}
                />
              </div>
            )}

            {/* Action buttons */}
            <div className="flex items-center gap-3 mt-5 justify-center sm:justify-start flex-wrap">
              {chapters.length > 0 ? (
                <Link
                  to={`/library/read/${bookId}/${hasProgress ? currentChapter : firstChapter}`}
                  className={`${t.btnPrimary} px-5 py-2.5 rounded-lg font-medium text-sm transition-colors inline-flex items-center gap-2`}
                >
                  <BookOpen size={16} />
                  {hasProgress ? `Continue Reading (Ch. ${currentChapter})` : 'Start Reading'}
                </Link>
              ) : (
                <span className={`${t.btnSecondary} px-5 py-2.5 rounded-lg font-medium text-sm opacity-50 cursor-not-allowed inline-flex items-center gap-2`}>
                  <BookOpen size={16} />
                  No chapters yet
                </span>
              )}
              <button
                onClick={() => handleEbookDownload('epub')}
                disabled={!!downloading}
                className={`${t.btnSecondary} px-4 py-2.5 rounded-lg font-medium text-sm transition-colors inline-flex items-center gap-2 ${downloading ? 'opacity-60 cursor-not-allowed' : ''}`}
              >
                {downloading === 'epub' ? <Loader2 size={16} className="animate-spin" /> : <Download size={16} />}
                {downloading === 'epub' ? 'Preparing...' : 'EPUB'}
              </button>
              {azw3_available && (
                <button
                  onClick={() => handleEbookDownload('azw3')}
                  disabled={!!downloading}
                  title="Kindle-compatible AZW3 file"
                  className={`${t.btnSecondary} px-4 py-2.5 rounded-lg font-medium text-sm transition-colors inline-flex items-center gap-2 ${downloading ? 'opacity-60 cursor-not-allowed' : ''}`}
                >
                  {downloading === 'azw3' ? <Loader2 size={16} className="animate-spin" /> : <Download size={16} />}
                  {downloading === 'azw3' ? 'Preparing...' : 'Kindle'}
                </button>
              )}
              <a
                href={`/api/public/books/${bookId}/feed.rss`}
                title="RSS feed of newly translated chapters for this book"
                className={`${t.btnSecondary} px-4 py-2.5 rounded-lg font-medium text-sm transition-colors inline-flex items-center gap-2`}
              >
                <Rss size={16} />
                RSS
              </a>
              {commentsEnabled && (
                <button
                  onClick={() => commentsModal.open()}
                  title="Discussion"
                  className={`${t.btnSecondary} px-4 py-2.5 rounded-lg font-medium text-sm transition-colors inline-flex items-center gap-2`}
                >
                  <MessageCircle size={16} />
                  Comments
                  {commentCount > 0 && (
                    <span className="min-w-[18px] h-[18px] px-1 rounded-full bg-indigo-600 text-white text-[10px] font-medium flex items-center justify-center">
                      {commentCount > 99 ? '99+' : commentCount}
                    </span>
                  )}
                </button>
              )}
            </div>
          </div>
        </div>

        {/* Synopsis */}
        {book.description && (
          <section className="mt-10">
            <h2 className={`text-lg font-semibold ${t.sectionTitle} mb-3`}>Synopsis</h2>
            <div className={`border-t ${t.divider} pt-4`}>
              <p className={`${t.description} whitespace-pre-line leading-relaxed`}>{book.description}</p>
            </div>
          </section>
        )}

        {/* Chapters */}
        {chapters.length > 0 && (
          <section className="mt-10">
            <h2 className={`text-lg font-semibold ${t.sectionTitle} mb-3`}>Chapters</h2>
            <div className={`border-t ${t.divider} md:columns-2 md:gap-x-6`}>
              {displayedChapters.map(ch => {
                const isCurrent = currentChapter === ch.chapter
                return (
                  <Link
                    key={ch.chapter}
                    to={`/library/read/${bookId}/${ch.chapter}`}
                    className={`flex items-center py-3 px-3 -mx-3 rounded transition-colors break-inside-avoid ${isCurrent ? t.progressHighlight : t.chapterRow} group`}
                  >
                    <span className={`w-16 flex-shrink-0 text-sm font-mono ${t.chapterNum}`}>
                      Ch {ch.chapter}
                    </span>
                    <span className={`flex-1 text-sm ${isCurrent ? t.title : t.chapterTitle} font-medium truncate`}>
                      {ch.title || `Chapter ${ch.chapter}`}
                    </span>
                    <ChevronRight size={16} className={`${t.chapterNum} opacity-0 group-hover:opacity-100 transition-opacity flex-shrink-0`} />
                  </Link>
                )
              })}
            </div>
            {chapters.length > INITIAL_CHAPTERS && !showAll && (
              <button
                onClick={() => setShowAll(true)}
                className={`mt-4 text-sm ${t.link} font-medium transition-colors`}
              >
                Show all {chapters.length} chapters
              </button>
            )}
          </section>
        )}
      </main>

      <ReaderComments
        open={commentsOpen}
        onClose={() => { commentsModal.close(); refreshCommentCount() }}
        bookId={Number(bookId)}
        chapterNumber={BOOK_DISCUSSION_CH}
        themeMode={prefs.theme}
      />

      {kindleToast && (
        <div
          role="status"
          className={`fixed bottom-6 left-1/2 -translate-x-1/2 z-50 max-w-md w-[calc(100%-2rem)]
                      bg-slate-800 rounded-lg shadow-xl px-4 py-3 flex items-start gap-3 text-sm text-slate-100
                      border ${kindleToast.type === 'error' ? 'border-red-700/60' : 'border-indigo-600/50'}`}
        >
          {kindleToast.type === 'error'
            ? <X size={16} className="text-red-400 mt-0.5 shrink-0" />
            : <Loader2 size={16} className="text-indigo-400 mt-0.5 shrink-0 animate-spin" />}
          <span className="flex-1 leading-snug">{kindleToast.msg}</span>
          <button
            className="text-slate-500 hover:text-slate-300 transition-colors mt-0.5 shrink-0"
            onClick={() => setKindleToast(null)}
            aria-label="Dismiss"
          >
            <X size={14} />
          </button>
        </div>
      )}
    </div>
  )
}
