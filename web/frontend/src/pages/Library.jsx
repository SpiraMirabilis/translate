import { useState, useEffect } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { BookOpen, Loader2, User, BookText, Sun, Moon, Sunset, MessageSquarePlus, Rss, X } from 'lucide-react'
import { useReaderPrefs } from '../hooks/useReaderPrefs'
import { useLocalStorage } from '../hooks/useLocalStorage'
import RecommendModal from '../components/RecommendModal'
import { useUrlModal } from '../hooks/useUrlState'
import { useSite } from '../App'
import { bustUrl } from '../services/cacheBust'
import { publicApi } from '../services/api'
import TagChips from '../components/TagChips'
import ProtagonistBadge from '../components/ProtagonistBadge'

const SORT_OPTIONS = [
  { id: 'popular',     label: 'Most Popular' },
  { id: 'updated',     label: 'Recently Updated' },
  { id: 'newly_added', label: 'Newly Added' },
  { id: 'title',       label: 'Title' },
]

// Maps a source-language code to a display tag. Covers the languages we
// currently translate from; colloquial variants (kr/jp) map alongside the
// official ISO 639 codes (ko/ja) so a book tags correctly however it was created.
// Add more codes here as new source languages come online.
const LANGUAGE_TAGS = {
  en: 'english',
  zh: 'chinese',
  ru: 'russian',
  ko: 'korean',   kr: 'korean',
  ja: 'japanese', jp: 'japanese',
}

// Prepend the source-language tag (if known) to a book's own tags,
// skipping it when the book already carries that tag explicitly.
function tagsWithLanguage(book) {
  const tags = book.tags || []
  const lang = LANGUAGE_TAGS[(book.source_language || '').toLowerCase()]
  if (!lang || tags.some(t => t.toLowerCase() === lang)) return tags
  return [lang, ...tags]
}

// A book is "new" if it was added within this many days.
const NEW_BOOK_DAYS = 31
// A book has "new updates" if its latest chapter posted within this many hours.
const RECENT_UPDATE_HOURS = 48

function isNewlyAdded(createdDate) {
  if (!createdDate) return false
  const created = new Date(createdDate)
  if (isNaN(created)) return false
  return (Date.now() - created.getTime()) < NEW_BOOK_DAYS * 24 * 60 * 60 * 1000
}

function hasRecentUpdate(lastChapterDate) {
  if (!lastChapterDate) return false
  const updated = new Date(lastChapterDate)
  if (isNaN(updated)) return false
  return (Date.now() - updated.getTime()) < RECENT_UPDATE_HOURS * 60 * 60 * 1000
}

const THEME_TOGGLE = [
  { id: 'light', icon: Sun,    label: 'Light' },
  { id: 'sepia', icon: Sunset, label: 'Sepia' },
  { id: 'dark',  icon: Moon,   label: 'Dark'  },
]

// Per-theme style tokens (beyond the bg/text from useReaderPrefs)
const T = {
  light: {
    headerBg: 'bg-white', headerBorder: 'border-stone-200',
    subtitle: 'text-gray-500', cardBg: 'bg-stone-200',
    placeholderFrom: 'from-indigo-100', placeholderTo: 'to-indigo-50',
    placeholderIcon: 'text-indigo-300', placeholderText: 'text-indigo-400',
    title: 'text-gray-900', titleHover: 'group-hover:text-indigo-600',
    author: 'text-gray-500', chapters: 'text-gray-400',
    toggleBg: 'bg-stone-100', toggleActive: 'bg-white text-indigo-600 shadow-sm',
    toggleInactive: 'text-gray-400 hover:text-gray-600',
    // Modal theme tokens
    modalBg: 'bg-white', modalText: 'text-gray-900', modalBorder: 'border-gray-200',
    inputBg: 'bg-gray-50', inputBorder: 'border-gray-300', inputText: 'text-gray-900',
    labelText: 'text-gray-700', subtleText: 'text-gray-500', turnstileTheme: 'light',
  },
  sepia: {
    headerBg: 'bg-amber-100/80', headerBorder: 'border-amber-200',
    subtitle: 'text-amber-800/60', cardBg: 'bg-amber-200/50',
    placeholderFrom: 'from-amber-200/60', placeholderTo: 'to-amber-100/60',
    placeholderIcon: 'text-amber-400', placeholderText: 'text-amber-600',
    title: 'text-amber-950', titleHover: 'group-hover:text-indigo-700',
    author: 'text-amber-800/60', chapters: 'text-amber-700/50',
    toggleBg: 'bg-amber-200/50', toggleActive: 'bg-amber-50 text-amber-900 shadow-sm',
    toggleInactive: 'text-amber-700/50 hover:text-amber-900',
    modalBg: 'bg-amber-50', modalText: 'text-amber-950', modalBorder: 'border-amber-200',
    inputBg: 'bg-amber-100/50', inputBorder: 'border-amber-300', inputText: 'text-amber-950',
    labelText: 'text-amber-800', subtleText: 'text-amber-700/60', turnstileTheme: 'light',
  },
  dark: {
    headerBg: 'bg-slate-800/80', headerBorder: 'border-slate-700',
    subtitle: 'text-slate-400', cardBg: 'bg-slate-800',
    placeholderFrom: 'from-slate-800', placeholderTo: 'to-slate-700',
    placeholderIcon: 'text-slate-500', placeholderText: 'text-slate-400',
    title: 'text-slate-100', titleHover: 'group-hover:text-indigo-400',
    author: 'text-slate-400', chapters: 'text-slate-500',
    toggleBg: 'bg-slate-800', toggleActive: 'bg-slate-700 text-slate-100 shadow-sm',
    toggleInactive: 'text-slate-500 hover:text-slate-300',
    modalBg: 'bg-slate-800', modalText: 'text-slate-100', modalBorder: 'border-slate-700',
    inputBg: 'bg-slate-700', inputBorder: 'border-slate-600', inputText: 'text-slate-100',
    labelText: 'text-slate-300', subtleText: 'text-slate-400', turnstileTheme: 'dark',
  },
}

export default function Library() {
  const [books, setBooks] = useState([])
  const [loading, setLoading] = useState(true)
  const [sort, setSort] = useLocalStorage('library.sort', 'popular')
  const recommendModal = useUrlModal('recommend')
  const { prefs, setPrefs, theme } = useReaderPrefs()
  const { site_name, public_site_name } = useSite()
  const t = T[prefs.theme] || T.light
  const [searchParams, setSearchParams] = useSearchParams()
  const activeTag = searchParams.get('tag')?.toLowerCase() || ''

  useEffect(() => {
    setLoading(true)
    publicApi.listBooks(sort)
      .then(data => setBooks(data.books || []))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [sort])

  const filteredBooks = activeTag
    ? books.filter(b => tagsWithLanguage(b).some(t => t.toLowerCase() === activeTag))
    : books

  const clearTag = () => {
    const next = new URLSearchParams(searchParams)
    next.delete('tag')
    setSearchParams(next, { replace: true })
  }

  const setTag = (tag) => {
    const next = new URLSearchParams(searchParams)
    next.set('tag', tag)
    setSearchParams(next)
  }

  useEffect(() => {
    document.title = public_site_name
    return () => { document.title = site_name }
  }, [public_site_name, site_name])

  const cycleTheme = (id) => setPrefs(p => ({ ...p, theme: id }))

  return (
    <div className={`min-h-screen ${theme.bg} ${theme.text} transition-colors duration-300`}>
      {/* Header */}
      <header className={`${t.headerBg} border-b ${t.headerBorder} shadow-sm transition-colors duration-300`}>
        <div className="max-w-6xl mx-auto px-6 py-6 sm:py-8 flex items-start justify-between">
          <div>
            <div className="flex items-center gap-3">
              <BookOpen size={28} className="text-indigo-500" />
              <h1 className={`text-2xl sm:text-3xl font-bold ${t.title} tracking-tight`}>{public_site_name}</h1>
            </div>
            <p className={`mt-1 ${t.subtitle} text-sm`}>Browse and read novels. Non-wordpress version.</p>
          </div>

          <div className="flex items-center gap-3">
            <select
              value={sort}
              onChange={(e) => setSort(e.target.value)}
              aria-label="Sort books"
              className={`text-sm rounded-md px-2 py-1.5 border-none focus:outline-none focus:ring-2 focus:ring-indigo-400 ${t.toggleBg} ${t.toggleInactive}`}
            >
              {SORT_OPTIONS.map(opt => (
                <option key={opt.id} value={opt.id}>{opt.label}</option>
              ))}
            </select>
            <a
              href="/api/public/feed.rss"
              title="RSS feed of recently translated chapters"
              className={`p-2 rounded-md transition-all duration-200 ${t.toggleInactive}`}
            >
              <Rss size={16} />
            </a>
            {/* Theme toggle */}
            <div className={`flex items-center gap-0.5 ${t.toggleBg} rounded-lg p-1 transition-colors duration-300`}>
              {THEME_TOGGLE.map(({ id, icon: Icon, label }) => (
                <button
                  key={id}
                  onClick={() => cycleTheme(id)}
                  title={label}
                  className={`p-2 rounded-md transition-all duration-200 ${prefs.theme === id ? t.toggleActive : t.toggleInactive}`}
                >
                  <Icon size={16} />
                </button>
              ))}
            </div>
          </div>
        </div>
      </header>

      {/* Content */}
      <main className="max-w-6xl mx-auto px-6 py-8">
        {activeTag && (
          <div className={`mb-6 flex items-center gap-2 flex-wrap text-sm ${t.subtitle}`}>
            <span>Filtering by tag:</span>
            <span className="px-2 py-0.5 rounded font-medium bg-indigo-500/20 text-indigo-400">{activeTag}</span>
            <span>· {filteredBooks.length} book{filteredBooks.length !== 1 ? 's' : ''}</span>
            <button
              onClick={clearTag}
              className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs ${t.toggleBg} ${t.toggleInactive} hover:text-indigo-400`}
            >
              <X size={11} /> Clear
            </button>
          </div>
        )}
        {loading ? (
          <div className="flex justify-center py-32">
            <Loader2 size={32} className="animate-spin text-indigo-400" />
          </div>
        ) : filteredBooks.length === 0 ? (
          <div className="text-center py-32">
            <BookText size={48} className="mx-auto mb-4 opacity-30" />
            <p className={`${t.subtitle} text-lg`}>
              {activeTag ? `No books tagged "${activeTag}"` : 'No books available yet'}
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-6">
            {filteredBooks.map(book => {
              const isNew = isNewlyAdded(book.created_date)
              const isUpdated = hasRecentUpdate(book.last_chapter_date)
              const tags = tagsWithLanguage(book)
              return (
              <div key={book.id} className="group">
                <Link to={`/library/book/${book.id}`} className="block">
                  {/* Cover */}
                  <div className={`aspect-[2/3] rounded-lg overflow-hidden ${t.cardBg} shadow-md group-hover:shadow-xl transition-shadow duration-300 relative`}>
                    {book.cover_image ? (
                      <img
                        src={bustUrl(book.cover_medium_url || `/api/public/books/${book.id}/cover/medium`)}
                        alt={book.title}
                        className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
                      />
                    ) : (
                      <div className={`w-full h-full flex flex-col items-center justify-center bg-gradient-to-br ${t.placeholderFrom} ${t.placeholderTo} p-4`}>
                        <BookOpen size={36} className={`${t.placeholderIcon} mb-3`} />
                        <span className={`text-sm font-medium ${t.placeholderText} text-center leading-tight`}>
                          {book.title}
                        </span>
                      </div>
                    )}
                    {/* "New" badge — top-left corner overlay */}
                    {isNew && (
                      <div className="absolute top-1.5 left-1.5">
                        <span className="px-1.5 py-0.5 rounded text-[10px] font-bold uppercase tracking-wide bg-amber-400 text-amber-950 shadow-sm">
                          New
                        </span>
                      </div>
                    )}
                    {/* Protagonist badge — top-right corner overlay */}
                    {tags.length > 0 && (
                      <div className="absolute top-1.5 right-1.5">
                        <ProtagonistBadge tags={tags} size="sm" showLabel={false} overlay />
                      </div>
                    )}
                    {/* Hover overlay */}
                    <div className="absolute inset-0 bg-black/0 group-hover:bg-black/10 transition-colors duration-300" />
                  </div>

                  {/* Info */}
                  <div className="mt-3">
                    <div className="flex items-center gap-1.5">
                      <h3 className={`font-semibold ${t.title} text-sm leading-tight line-clamp-2 ${t.titleHover} transition-colors`}>
                        {book.title}
                      </h3>
                    </div>
                    {book.author && (
                      <p className={`flex items-center gap-1 mt-1 text-xs ${t.author}`}>
                        <User size={11} />
                        {book.author}
                      </p>
                    )}
                    <p className={`mt-0.5 text-xs ${t.chapters} flex items-center gap-1 flex-wrap`}>
                      <span>
                        {book.chapter_count} chapter{book.chapter_count !== 1 ? 's' : ''}
                        {book.total_source_chapters > 0 && (
                          <> / {book.total_source_chapters} ({Math.round((book.chapter_count / book.total_source_chapters) * 100)}%)</>
                        )}
                      </span>
                      {isUpdated && (
                        <span className="px-1.5 py-0 rounded text-[10px] font-medium bg-emerald-500/20 text-emerald-400">
                          Updated
                        </span>
                      )}
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
                  </div>
                </Link>
                {tags.length > 0 && (
                  <div className="mt-1.5">
                    <TagChips tags={tags} size="sm" theme={prefs.theme} onTagClick={setTag} />
                  </div>
                )}
              </div>
              )
            })}

            {/* Recommend a Novel card */}
            <button
              onClick={() => recommendModal.open()}
              className="group block text-left"
            >
              <div className={`aspect-[2/3] rounded-lg overflow-hidden ${t.cardBg} shadow-md group-hover:shadow-xl transition-shadow duration-300 relative cursor-pointer`}>
                <div className={`w-full h-full flex flex-col items-center justify-center bg-gradient-to-br ${t.placeholderFrom} ${t.placeholderTo} p-4`}>
                  <MessageSquarePlus size={36} className={`${t.placeholderIcon} mb-3 group-hover:scale-110 transition-transform duration-300`} />
                  <span className={`text-sm font-medium ${t.placeholderText} text-center leading-tight`}>
                    Recommend a Novel
                  </span>
                </div>
                <div className="absolute inset-0 bg-black/0 group-hover:bg-black/10 transition-colors duration-300" />
              </div>
              <div className="mt-3">
                <h3 className={`font-semibold ${t.title} text-sm leading-tight ${t.titleHover} transition-colors`}>
                  Recommend a Novel
                </h3>
                <p className={`mt-0.5 text-xs ${t.chapters}`}>
                  Suggest a novel for translation
                </p>
              </div>
            </button>
          </div>
        )}
      </main>

      {recommendModal.isOpen && (
        <RecommendModal
          onClose={recommendModal.close}
          theme={t}
        />
      )}
    </div>
  )
}
