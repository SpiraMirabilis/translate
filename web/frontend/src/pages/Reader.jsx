import { useState, useEffect, useCallback, useRef } from 'react'
import { useParams, useSearchParams, useLocation, Link } from 'react-router-dom'
import { api } from '../services/api'
import { useLocalStorage } from '../hooks/useLocalStorage'
import { useReaderPrefs } from '../hooks/useReaderPrefs'
import ReaderTOC from '../components/ReaderTOC'
import ReaderSettings from '../components/ReaderSettings'
import ReaderSearch from '../components/ReaderSearch'
import {
  ArrowLeft, List, Settings2, ChevronLeft, ChevronRight, Loader2, Maximize, Minimize, Search
} from 'lucide-react'

// Public API for unauthenticated access — mirrors the shape of the
// authenticated api object but hits /api/public/* endpoints.
const publicApi = {
  getBook:       (id)         => fetch(`/api/public/books/${id}`, { credentials: 'same-origin' }).then(r => { if (!r.ok) throw new Error(r.status); return r.json() }),
  listChapters:  (bookId)     => fetch(`/api/public/books/${bookId}/chapters`, { credentials: 'same-origin' }).then(r => { if (!r.ok) throw new Error(r.status); return r.json() }),
  getChapter:    (bookId, n)  => fetch(`/api/public/books/${bookId}/chapters/${n}`, { credentials: 'same-origin' }).then(r => { if (!r.ok) throw new Error(r.status); return r.json() }),
  searchBook:    (bookId, b)  => fetch(`/api/public/books/${bookId}/search`, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b) }).then(r => { if (!r.ok) throw new Error(r.status); return r.json() }),
}

export default function Reader({ isPublic = false }) {
  const { bookId, chapterNum: chapterNumParam } = useParams()
  const [searchParams] = useSearchParams()
  const location = useLocation()
  const { prefs, setPrefs, theme, contentStyle, marginClass } = useReaderPrefs()
  const [progress, setProgress] = useLocalStorage('reader-progress', {})

  // Use public or authenticated API depending on context
  const readerApi = isPublic ? publicApi : api
  // Detect if we're under the /library prefix so links stay consistent
  const libraryPrefix = location.pathname.startsWith('/library/')
  const backPath = isPublic ? '/library' : '/books'

  const [book, setBook] = useState(null)
  const [chapters, setChapters] = useState([])
  const [currentNum, setCurrentNum] = useState(null)
  const [chapter, setChapter] = useState(null)
  const [loading, setLoading] = useState(true)
  const [chapterLoading, setChapterLoading] = useState(false)
  const [tocOpen, setTocOpen] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [searchOpen, setSearchOpen] = useState(false)
  const [isFullscreen, setIsFullscreen] = useState(false)
  const [barVisible, setBarVisible] = useState(true)

  const contentRef = useRef(null)
  const barTimer = useRef(null)

  // Load book + chapter list
  useEffect(() => {
    let cancelled = false
    async function load() {
      try {
        const [bookData, chData] = await Promise.all([
          readerApi.getBook(bookId),
          readerApi.listChapters(bookId),
        ])
        if (cancelled) return
        setBook(bookData)
        const sorted = (chData.chapters || []).sort((a, b) => a.chapter - b.chapter)
        setChapters(sorted)

        // Determine initial chapter
        const fromRoute = chapterNumParam ? +chapterNumParam : null
        const fromQuery = searchParams.get('chapter') ? +searchParams.get('chapter') : null
        const fromStorage = progress[bookId]
        const initial = fromRoute || fromQuery || fromStorage || sorted[0]?.chapter || 1
        setCurrentNum(initial)
      } catch {
        // book not found or error
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => { cancelled = true }
  }, [bookId]) // eslint-disable-line react-hooks/exhaustive-deps

  // Load chapter content
  useEffect(() => {
    if (currentNum == null) return
    let cancelled = false
    async function load() {
      setChapterLoading(true)
      try {
        const data = await readerApi.getChapter(bookId, currentNum)
        if (!cancelled) setChapter(data)
      } catch {
        if (!cancelled) setChapter(null)
      } finally {
        if (!cancelled) setChapterLoading(false)
      }
    }
    load()
    return () => { cancelled = true }
  }, [bookId, currentNum])

  // Page title
  useEffect(() => {
    const parts = []
    if (chapter?.title) parts.push(chapter.title)
    else if (currentNum != null) parts.push(`Chapter ${currentNum}`)
    if (book?.title) parts.push(book.title)
    document.title = parts.length > 0 ? parts.join(' — ') : 'Reader'
    return () => { document.title = 'T9' }
  }, [book, chapter, currentNum])

  // Save progress + update URL
  useEffect(() => {
    if (currentNum != null) {
      setProgress(prev => ({ ...prev, [bookId]: currentNum }))
      const base = libraryPrefix ? `/library/read/${bookId}` : `/read/${bookId}`
      window.history.replaceState(null, '', `${base}/${currentNum}`)
    }
  }, [currentNum, bookId, setProgress, libraryPrefix])

  // Scroll to top on chapter change
  useEffect(() => {
    contentRef.current?.scrollTo(0, 0)
  }, [currentNum])

  // Fullscreen
  const toggleFullscreen = useCallback(() => {
    if (!document.fullscreenElement) {
      document.documentElement.requestFullscreen().catch(() => {})
    } else {
      document.exitFullscreen().catch(() => {})
    }
  }, [])

  useEffect(() => {
    const onChange = () => setIsFullscreen(!!document.fullscreenElement)
    document.addEventListener('fullscreenchange', onChange)
    return () => document.removeEventListener('fullscreenchange', onChange)
  }, [])

  // Keyboard nav + Ctrl+F
  useEffect(() => {
    function onKey(e) {
      if ((e.ctrlKey || e.metaKey) && e.key === 'f') {
        e.preventDefault()
        setSearchOpen(true)
        return
      }
      if (e.key === 'Escape' && searchOpen) {
        setSearchOpen(false)
        return
      }
      if (tocOpen || settingsOpen || searchOpen) return
      if (e.key === 'ArrowLeft') goChapter(-1)
      if (e.key === 'ArrowRight') goChapter(1)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }) // intentionally no deps — uses latest closure

  // Auto-hide top bar on scroll
  const lastScroll = useRef(0)
  const handleScroll = useCallback(() => {
    const el = contentRef.current
    if (!el) return
    const y = el.scrollTop
    if (y > lastScroll.current && y > 80) {
      setBarVisible(false)
    } else {
      setBarVisible(true)
    }
    lastScroll.current = y
  }, [])

  const currentIdx = chapters.findIndex(c => c.chapter === currentNum)
  const prevChapter = currentIdx > 0 ? chapters[currentIdx - 1] : null
  const nextChapter = currentIdx < chapters.length - 1 ? chapters[currentIdx + 1] : null

  const goChapter = useCallback((dir) => {
    const idx = chapters.findIndex(c => c.chapter === currentNum)
    const target = chapters[idx + dir]
    if (target) setCurrentNum(target.chapter)
  }, [chapters, currentNum])

  // Swipe gesture navigation
  const touchStart = useRef(null)
  const touchStartY = useRef(null)
  const touchStartTime = useRef(null)
  const handleTouchStart = useCallback((e) => {
    if (tocOpen || settingsOpen || searchOpen) return
    touchStart.current = e.touches[0].clientX
    touchStartY.current = e.touches[0].clientY
    touchStartTime.current = Date.now()
  }, [tocOpen, settingsOpen, searchOpen])

  const handleTouchEnd = useCallback((e) => {
    if (touchStart.current === null) return
    const dx = e.changedTouches[0].clientX - touchStart.current
    const dy = e.changedTouches[0].clientY - touchStartY.current
    const elapsed = Date.now() - touchStartTime.current
    touchStart.current = null
    touchStartY.current = null
    touchStartTime.current = null
    // Ignore if user is selecting text
    const selection = window.getSelection()
    if (selection && selection.toString().length > 0) return
    // Must be a quick swipe (under 2s), min 80px horizontal, and more horizontal than vertical
    if (elapsed < 2000 && Math.abs(dx) > 80 && Math.abs(dx) > Math.abs(dy) * 1.5) {
      if (dx > 0) goChapter(-1)  // swipe right → previous
      else goChapter(1)           // swipe left → next
    }
  }, [goChapter])

  // Theme-driven classes
  const isDark = prefs.theme === 'dark'
  const barBg = isDark ? 'bg-slate-900/95 border-slate-700' : prefs.theme === 'sepia' ? 'bg-amber-100/95 border-amber-200' : 'bg-white/95 border-stone-200'
  const barText = isDark ? 'text-slate-300' : 'text-gray-600'
  const barTextStrong = isDark ? 'text-slate-100' : 'text-gray-900'
  const navBtnClass = isDark
    ? 'bg-slate-800 text-slate-300 hover:bg-slate-700 border-slate-700'
    : prefs.theme === 'sepia'
      ? 'bg-amber-100 text-amber-900 hover:bg-amber-200 border-amber-200'
      : 'bg-stone-100 text-gray-700 hover:bg-stone-200 border-stone-200'

  if (loading) {
    return (
      <div className={`min-h-screen ${theme.bg} flex items-center justify-center`}>
        <Loader2 size={32} className="animate-spin text-indigo-400" />
      </div>
    )
  }

  if (!book) {
    return (
      <div className={`min-h-screen ${theme.bg} flex items-center justify-center`}>
        <div className="text-center">
          <p className={`${theme.text} text-lg`}>Book not found</p>
          <Link to={backPath} className="text-indigo-400 hover:underline mt-2 inline-block">{isPublic ? 'Back to Library' : 'Back to Books'}</Link>
        </div>
      </div>
    )
  }

  const contentMode = prefs.contentMode || 'translated'
  const hasSource = !!(chapter?.untranslated?.length)
  const translatedLines = chapter?.content || []
  const sourceLines = chapter?.untranslated || []

  return (
    <div className={`min-h-screen ${theme.bg} ${theme.text} transition-colors duration-300`}>
      {/* Top bar */}
      <div className={`fixed top-0 left-0 right-0 z-30 border-b backdrop-blur-sm transition-transform duration-300
        ${barBg} ${barVisible ? 'translate-y-0' : '-translate-y-full'}`}>
        <div className="max-w-4xl mx-auto px-4 h-12 flex items-center gap-3">
          <Link to={backPath} className={`${barText} hover:${barTextStrong} p-1`} title={isPublic ? 'Back to Library' : 'Back to Books'}>
            <ArrowLeft size={20} />
          </Link>
          <div className="flex-1 min-w-0 text-center">
            <span className={`text-sm font-medium ${barTextStrong} truncate block`}>
              {book.title}
            </span>
            {chapter && (
              <span className={`text-xs ${barText} truncate block`}>
                Chapter {chapter.chapter}{chapter.title ? `: ${chapter.title}` : ''}
              </span>
            )}
          </div>
          <button onClick={() => setTocOpen(true)} className={`${barText} hover:${barTextStrong} p-1.5`} title="Table of Contents">
            <List size={20} />
          </button>
          <button onClick={() => setSearchOpen(true)} className={`${barText} hover:${barTextStrong} p-1.5`} title="Search (Ctrl+F)">
            <Search size={20} />
          </button>
          <button onClick={toggleFullscreen} className={`${barText} hover:${barTextStrong} p-1.5`} title={isFullscreen ? 'Exit Full Screen' : 'Full Screen'}>
            {isFullscreen ? <Minimize size={20} /> : <Maximize size={20} />}
          </button>
          <button onClick={() => setSettingsOpen(true)} className={`${barText} hover:${barTextStrong} p-1.5`} title="Settings">
            <Settings2 size={20} />
          </button>
        </div>
      </div>

      {/* Content area */}
      <div
        ref={contentRef}
        className="h-screen overflow-y-auto pt-14 pb-8"
        onScroll={handleScroll}
        onTouchStart={handleTouchStart}
        onTouchEnd={handleTouchEnd}
      >
        {chapterLoading ? (
          <div className="flex justify-center py-32">
            <Loader2 size={28} className="animate-spin text-indigo-400" />
          </div>
        ) : (
          <article className={`${marginClass} mx-auto px-6 py-8 sm:px-8 transition-all duration-300`}>
            {/* Chapter heading */}
            <header className="mb-8 text-center">
              <h1 className="text-2xl font-bold" style={{ fontFamily: contentStyle.fontFamily }}>
                Chapter {chapter?.chapter}
              </h1>
              {chapter?.title && (
                <h2 className={`text-lg mt-1 ${isDark ? 'text-slate-400' : prefs.theme === 'sepia' ? 'text-amber-800/70' : 'text-gray-500'}`}
                    style={{ fontFamily: contentStyle.fontFamily }}>
                  {chapter.title}
                </h2>
              )}
            </header>

            {/* Chapter text */}
            <div style={contentStyle}>
              {contentMode === 'both' && hasSource ? (
                // Interleaved: source line then translated line
                translatedLines.map((line, i) => {
                  const src = sourceLines[i]
                  const isEmpty = (!line || !line.trim()) && (!src || !src.trim())
                  if (isEmpty) return <div key={i} className="h-4" />
                  return (
                    <div key={i} className="mb-4">
                      {src && src.trim() && (
                        <p className={`mb-1 text-[0.85em] ${isDark ? 'text-slate-500' : prefs.theme === 'sepia' ? 'text-amber-800/50' : 'text-gray-400'}`}>
                          {src}
                        </p>
                      )}
                      {line && line.trim() && <p>{line}</p>}
                    </div>
                  )
                })
              ) : (
                // Single mode: source or translated
                (contentMode === 'source' && hasSource ? sourceLines : translatedLines).map((line, i) => {
                  if (!line || !line.trim()) return <div key={i} className="h-4" />
                  return <p key={i} className="mb-4">{line}</p>
                })
              )}
            </div>

            {/* Bottom navigation */}
            <nav className="mt-16 mb-8 flex items-center justify-between gap-4">
              {prevChapter ? (
                <button
                  onClick={() => setCurrentNum(prevChapter.chapter)}
                  className={`flex items-center gap-2 px-4 py-2.5 rounded-lg border transition-colors text-sm ${navBtnClass}`}
                >
                  <ChevronLeft size={16} />
                  <div className="text-left">
                    <div className="text-xs opacity-60">Previous</div>
                    <div className="truncate max-w-[140px]">{prevChapter.title || `Ch. ${prevChapter.chapter}`}</div>
                  </div>
                </button>
              ) : <div />}
              {nextChapter ? (
                <button
                  onClick={() => setCurrentNum(nextChapter.chapter)}
                  className={`flex items-center gap-2 px-4 py-2.5 rounded-lg border transition-colors text-sm ${navBtnClass}`}
                >
                  <div className="text-right">
                    <div className="text-xs opacity-60">Next</div>
                    <div className="truncate max-w-[140px]">{nextChapter.title || `Ch. ${nextChapter.chapter}`}</div>
                  </div>
                  <ChevronRight size={16} />
                </button>
              ) : (
                <div className={`text-sm ${isDark ? 'text-slate-500' : 'text-gray-400'} italic`}>
                  End of book
                </div>
              )}
            </nav>
          </article>
        )}
      </div>

      {/* Drawers */}
      <ReaderTOC
        open={tocOpen}
        onClose={() => setTocOpen(false)}
        book={book}
        chapters={chapters}
        currentChapter={currentNum}
        onSelect={setCurrentNum}
        isPublic={isPublic}
        theme={prefs.theme}
      />
      <ReaderSettings
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        prefs={prefs}
        setPrefs={setPrefs}
        hasSource={hasSource}
      />
      <ReaderSearch
        open={searchOpen}
        onClose={() => setSearchOpen(false)}
        bookId={bookId}
        onNavigate={setCurrentNum}
        theme={prefs.theme}
        api={readerApi}
      />
    </div>
  )
}
