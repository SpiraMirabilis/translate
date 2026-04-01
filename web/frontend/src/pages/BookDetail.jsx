import { useState, useEffect } from 'react'
import { useParams, Link } from 'react-router-dom'
import { BookOpen, Loader2, ArrowLeft, Download, ChevronRight, Sun, Moon, Sunset, User, BookText } from 'lucide-react'
import { useReaderPrefs } from '../hooks/useReaderPrefs'
import { useLocalStorage } from '../hooks/useLocalStorage'

const publicApi = {
  getBook: (id) => fetch(`/api/public/books/${id}`, { credentials: 'same-origin' }).then(r => { if (!r.ok) throw new Error(r.status); return r.json() }),
  listChapters: (id) => fetch(`/api/public/books/${id}/chapters`, { credentials: 'same-origin' }).then(r => r.json()),
}

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
  const [book, setBook] = useState(null)
  const [chapters, setChapters] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [showAll, setShowAll] = useState(false)
  const { prefs, setPrefs, theme } = useReaderPrefs()
  const [progress] = useLocalStorage('reader-progress', {})
  const t = T[prefs.theme] || T.light

  useEffect(() => {
    Promise.all([
      publicApi.getBook(bookId),
      publicApi.listChapters(bookId),
    ])
      .then(([bookData, chapData]) => {
        setBook(bookData)
        setChapters(chapData.chapters || [])
      })
      .catch(() => setError(true))
      .finally(() => setLoading(false))
  }, [bookId])

  useEffect(() => {
    if (book) document.title = `${book.title} — Boonnovels`
    return () => { document.title = 'T9' }
  }, [book])

  const cycleTheme = (id) => setPrefs(p => ({ ...p, theme: id }))

  const currentChapter = progress?.[bookId]
  const hasProgress = currentChapter && currentChapter > 1

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
            <span className={`text-lg font-bold ${t.title} tracking-tight hidden sm:inline`}>Boonnovels</span>
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
                  src={`/api/public/books/${bookId}/cover`}
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
            <h1 className={`text-2xl sm:text-3xl font-bold ${t.title} leading-tight`}>{book.title}</h1>
            {book.author && (
              <p className={`flex items-center gap-1.5 mt-2 ${t.author} justify-center sm:justify-start`}>
                <User size={14} />
                {book.author}
              </p>
            )}
            <p className={`mt-1 text-sm ${t.meta}`}>
              {chapters.length} chapter{chapters.length !== 1 ? 's' : ''}
              {book.source_language && ` \u00b7 Source: ${book.source_language}`}
            </p>

            {/* Action buttons */}
            <div className="flex items-center gap-3 mt-5 justify-center sm:justify-start flex-wrap">
              {chapters.length > 0 ? (
                <Link
                  to={hasProgress ? `/library/read/${bookId}/${currentChapter}` : `/library/read/${bookId}`}
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
              <a
                href={`/api/public/books/${bookId}/epub`}
                download
                className={`${t.btnSecondary} px-4 py-2.5 rounded-lg font-medium text-sm transition-colors inline-flex items-center gap-2`}
              >
                <Download size={16} />
                EPUB
              </a>
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
            <div className={`border-t ${t.divider}`}>
              {displayedChapters.map(ch => {
                const isCurrent = currentChapter === ch.chapter
                return (
                  <Link
                    key={ch.chapter}
                    to={`/library/read/${bookId}/${ch.chapter}`}
                    className={`flex items-center py-3 px-3 -mx-3 rounded transition-colors ${isCurrent ? t.progressHighlight : t.chapterRow} group`}
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
    </div>
  )
}
