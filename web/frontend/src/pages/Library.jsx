import { useCallback, useEffect, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import { BookOpen, BookText, Sun, Moon, Sunset, MessageSquarePlus, Rss, Sparkles, Clock, Star, BookMarked, LibraryBig } from 'lucide-react'
import { useReaderPrefs } from '../hooks/useReaderPrefs'
import { useLocalStorage } from '../hooks/useLocalStorage'
import RecommendModal from '../components/RecommendModal'
import ErrorState from '../components/ErrorState'
import { useUrlModal } from '../hooks/useUrlState'
import { useSite } from '../App'
import { publicApi } from '../services/api'
import { applyFilters, buildShelves, hasActiveFilters, libraryStats, EMPTY_FILTERS } from '../lib/libraryFilter'
import { LIBRARY_THEME } from '../components/library/theme'
import BookCard from '../components/library/BookCard'
import BookCardSkeleton from '../components/library/BookCardSkeleton'
import BookShelf from '../components/library/BookShelf'
import ContinueReadingCard from '../components/library/ContinueReadingCard'
import LibraryToolbar from '../components/library/LibraryToolbar'

const SORT_OPTIONS = [
  { id: 'trending',    label: 'Trending' },
  { id: 'popular',     label: 'Most Popular' },
  { id: 'updated',     label: 'Recently Updated' },
  { id: 'newly_added', label: 'Newly Added' },
  { id: 'title',       label: 'Title' },
]

const THEME_TOGGLE = [
  { id: 'light', icon: Sun,    label: 'Light' },
  { id: 'sepia', icon: Sunset, label: 'Sepia' },
  { id: 'dark',  icon: Moon,   label: 'Dark'  },
]

const GRID = 'grid grid-cols-[repeat(auto-fill,minmax(8.5rem,1fr))] sm:grid-cols-[repeat(auto-fill,minmax(10rem,1fr))] gap-x-5 gap-y-8'

// Filters live in the query string so a narrowed view is a shareable link:
// ?q=…&tag=a,b&status=completed&lang=korean. The old single ?tag= deep link
// (from card chips, the book page) still reads correctly.
function readFilters(sp) {
  return {
    q: sp.get('q') || '',
    tags: (sp.get('tag') || '').split(',').map(s => s.trim().toLowerCase()).filter(Boolean),
    status: sp.get('status') || '',
    lang: (sp.get('lang') || '').toLowerCase(),
  }
}

export default function Library() {
  const [sort, setSort] = useLocalStorage('library.sort', 'trending')
  const recommendModal = useUrlModal('recommend')
  const { prefs, setPrefs, theme } = useReaderPrefs()
  const { site_name, public_site_name } = useSite()
  const t = LIBRARY_THEME[prefs.theme] || LIBRARY_THEME.light
  const [searchParams, setSearchParams] = useSearchParams()
  const [progress] = useLocalStorage('reader-progress', {})

  const filters = useMemo(() => readFilters(searchParams), [searchParams])
  const updateFilters = useCallback((patch) => {
    setSearchParams(prev => {
      const next = new URLSearchParams(prev)
      const f = { ...readFilters(prev), ...patch }
      f.q ? next.set('q', f.q) : next.delete('q')
      f.tags?.length ? next.set('tag', f.tags.join(',')) : next.delete('tag')
      f.status ? next.set('status', f.status) : next.delete('status')
      f.lang ? next.set('lang', f.lang) : next.delete('lang')
      return next
    }, { replace: true })
  }, [setSearchParams])
  const addTag = useCallback((tag) => {
    const lower = tag.toLowerCase()
    updateFilters({ tags: filters.tags.includes(lower) ? filters.tags : [...filters.tags, lower] })
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }, [filters.tags, updateFilters])

  const { data, isPending: loading, error, refetch } = useQuery({
    queryKey: ['public', 'books', sort],
    queryFn: () => publicApi.listBooks(sort),
  })
  const books = useMemo(() => data?.books || [], [data])
  const filtering = hasActiveFilters(filters)
  const filtered = useMemo(() => (filtering ? applyFilters(books, filters) : books), [books, filters, filtering])
  const shelves = useMemo(() => (filtering ? null : buildShelves(books, progress)), [books, progress, filtering])
  const stats = useMemo(() => libraryStats(books), [books])
  const now = Date.now()

  useEffect(() => {
    document.title = public_site_name
    return () => { document.title = site_name }
  }, [public_site_name, site_name])

  const card = (book) => <BookCard key={book.id} book={book} t={t} themeName={prefs.theme} onTagClick={addTag} now={now} />

  const recommendCard = (
    <button key="recommend" onClick={() => recommendModal.open()} className="group block text-left min-w-0">
      <div className={`aspect-[2/3] rounded-lg overflow-hidden ${t.cardBg} shadow-md group-hover:shadow-xl transition-shadow duration-300 relative border-2 border-dashed ${t.divider}`}>
        <div className="w-full h-full flex flex-col items-center justify-center p-4">
          <MessageSquarePlus size={32} className={`${t.chapters} mb-3 group-hover:scale-110 group-hover:text-indigo-500 transition-all duration-300`} />
          <span className={`text-sm font-medium ${t.author} text-center leading-tight`}>Recommend a Novel</span>
        </div>
      </div>
      <h3 className={`mt-2 font-semibold ${t.title} text-sm leading-tight ${t.titleHover} transition-colors`}>Recommend a Novel</h3>
      <p className={`mt-0.5 text-xs ${t.chapters}`}>Suggest one for translation</p>
    </button>
  )

  return (
    <div className={`min-h-screen ${theme.bg} ${theme.text} transition-colors duration-300`}>
      <header className={`${t.headerBg} border-b ${t.headerBorder} shadow-sm transition-colors duration-300`}>
        <div className="max-w-6xl mx-auto px-4 sm:px-6 pt-5 pb-4 sm:pt-7 sm:pb-5">
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <div className="flex items-center gap-3">
                <BookOpen size={28} className="text-indigo-500 shrink-0" />
                <h1 className={`text-2xl sm:text-3xl font-bold ${t.title} tracking-tight truncate`}>{public_site_name}</h1>
              </div>
              <p className={`mt-1 ${t.subtitle} text-sm tabular-nums`}>
                {loading ? 'Loading the shelves…' : (
                  <>
                    {stats.books.toLocaleString()} novels · {stats.chapters.toLocaleString()} chapters
                    {stats.updatedThisWeek > 0 && <span className="hidden sm:inline"> · {stats.updatedThisWeek} updated this week</span>}
                  </>
                )}
              </p>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              <a href="/api/public/feed.rss" title="RSS feed of recently translated chapters"
                 className={`p-2 rounded-md transition-all duration-200 ${t.toggleInactive}`}>
                <Rss size={16} />
              </a>
              <div className={`flex items-center gap-0.5 ${t.toggleBg} rounded-lg p-1 transition-colors duration-300`}>
                {THEME_TOGGLE.map(({ id, icon: Icon, label }) => (
                  <button key={id} onClick={() => setPrefs(p => ({ ...p, theme: id }))} title={label}
                    className={`p-2 rounded-md transition-all duration-200 ${prefs.theme === id ? t.toggleActive : t.toggleInactive}`}>
                    <Icon size={16} />
                  </button>
                ))}
              </div>
            </div>
          </div>
        </div>
      </header>

      {/* Sticky toolbar: search, sort, filters */}
      <div className={`sticky top-0 z-20 ${t.headerBg} border-b ${t.headerBorder} backdrop-blur transition-colors duration-300`}>
        <div className="max-w-6xl mx-auto px-4 sm:px-6 py-3">
          <LibraryToolbar
            books={books} filters={filters} onChange={updateFilters}
            sort={sort} setSort={setSort} sortOptions={SORT_OPTIONS}
            resultCount={filtering ? filtered.length : null} t={t}
          />
        </div>
      </div>

      <main className="max-w-6xl mx-auto px-4 sm:px-6 py-8">
        {loading ? (
          <div className={GRID}>
            {Array.from({ length: 15 }, (_, i) => <BookCardSkeleton key={i} t={t} />)}
          </div>
        ) : error ? (
          <ErrorState size="page" className={t.title} message="Couldn't load the library"
            detail={error.message || 'The connection may have dropped. Try again.'}
            onRetry={refetch} buttonClassName={t.errorButton} />
        ) : filtering ? (
          filtered.length === 0 ? (
            <div className="text-center py-32">
              <BookText size={48} className="mx-auto mb-4 opacity-30" />
              <p className={`${t.subtitle} text-lg`}>No books match these filters</p>
              <button onClick={() => updateFilters({ ...EMPTY_FILTERS })} className="mt-3 text-sm text-indigo-500 hover:underline">Clear filters</button>
            </div>
          ) : (
            <div className={GRID}>{filtered.map(card)}{recommendCard}</div>
          )
        ) : (
          <>
            {shelves.continue.length > 0 && (
              <BookShelf title="Continue reading" icon={BookMarked} count={shelves.continue.length} t={t}>
                {shelves.continue.map(({ book, chapter }) => (
                  <ContinueReadingCard key={book.id} book={book} chapter={chapter} t={t} themeName={prefs.theme} />
                ))}
              </BookShelf>
            )}
            {shelves.updated.length > 0 && (
              <BookShelf title="Recently updated" icon={Clock} count={shelves.updated.length} t={t}
                action={{ label: 'Sort by updated', onClick: () => setSort('updated') }}>
                {shelves.updated.map(card)}
              </BookShelf>
            )}
            {shelves.fresh.length > 0 && (
              <BookShelf title="New this month" icon={Sparkles} count={shelves.fresh.length} t={t}
                action={{ label: 'Sort by newest', onClick: () => setSort('newly_added') }}>
                {shelves.fresh.map(card)}
              </BookShelf>
            )}
            {shelves.picks.length > 0 && (
              <BookShelf title="Editor's choice" icon={Star} count={shelves.picks.length} t={t}
                action={{ label: 'See all', onClick: () => updateFilters({ tags: ["editor's choice"] }) }}>
                {shelves.picks.map(card)}
              </BookShelf>
            )}
            <section>
              <div className="flex items-baseline justify-between mb-4">
                <h2 className={`flex items-center gap-2 text-base font-semibold ${t.shelfTitle}`}>
                  <LibraryBig size={16} className="text-indigo-500" /> All books
                  <span className={`text-xs font-normal ${t.shelfMeta}`}>{SORT_OPTIONS.find(o => o.id === sort)?.label}</span>
                </h2>
              </div>
              {books.length === 0 ? (
                <div className="text-center py-32">
                  <BookText size={48} className="mx-auto mb-4 opacity-30" />
                  <p className={`${t.subtitle} text-lg`}>No books available yet</p>
                </div>
              ) : (
                <div className={GRID}>{books.map(card)}{recommendCard}</div>
              )}
            </section>
          </>
        )}
      </main>

      {recommendModal.isOpen && <RecommendModal onClose={recommendModal.close} theme={t} />}
    </div>
  )
}
