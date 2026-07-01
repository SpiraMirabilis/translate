import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import { useParams, useSearchParams, useLocation, Link } from 'react-router-dom'
import { api, publicApi } from '../services/api'
import { bustUrl } from '../services/cacheBust'
import { useLocalStorage } from '../hooks/useLocalStorage'
import { useReaderPrefs } from '../hooks/useReaderPrefs'
import { useUrlModal } from '../hooks/useUrlState'
import ReaderTOC from '../components/ReaderTOC'
import ReaderSettings from '../components/ReaderSettings'
import ReaderSearch from '../components/ReaderSearch'
import ReaderComments from '../components/ReaderComments'
import EntityFormModal from '../components/EntityFormModal'
import { loadIdentity } from '../components/CommentForm'
import { CATEGORY_COLORS } from '../utils/categories'
import { renderBlock, renderInline, splitSegments, parseFootnotes, markFootnoteLine, markFootnoteRefs, linkifyFootnotes } from '../lib/chapterMarkdown'
import FootnotePopover from '../components/FootnotePopover'
import ErrorState from '../components/ErrorState'
import { useSite } from '../App'
import {
  ArrowLeft, List, Settings2, ChevronLeft, ChevronRight, Loader2, Maximize, Minimize, Search, MessageCircle
} from 'lucide-react'

// In-chapter illustrations are stored in content as a line ⟦IMG:<id>⟧.
const IMG_MARKER_RE = /^\s*⟦IMG:([0-9a-f]{4,})⟧\s*$/
const illustrationId = (line) => {
  if (typeof line !== 'string') return null
  const m = line.match(IMG_MARKER_RE)
  return m ? m[1] : null
}

export default function Reader({ isPublic = false }) {
  const { bookId, chapterNum: chapterNumParam } = useParams()
  const [searchParams] = useSearchParams()
  const location = useLocation()
  const { prefs, setPrefs, theme, contentStyle, marginClass } = useReaderPrefs()
  const [progress, setProgress] = useLocalStorage('reader-progress', {})
  const { site_name, public_site_name } = useSite()

  // Use public or authenticated API depending on context
  const readerApi = isPublic ? publicApi : api
  // Detect if we're under the /library prefix so links stay consistent
  const libraryPrefix = location.pathname.startsWith('/library/')
  const backPath = isPublic ? '/library' : '/books'

  const [book, setBook] = useState(null)
  const [chapters, setChapters] = useState([])
  const [currentNum, setCurrentNum] = useState(null)
  const [chapter, setChapter] = useState(null)
  const [chapterError, setChapterError] = useState(null)
  const [reloadTick, setReloadTick] = useState(0)
  const [bookError, setBookError] = useState(null)
  const [bookReloadTick, setBookReloadTick] = useState(0)
  const [loading, setLoading] = useState(true)
  const [chapterLoading, setChapterLoading] = useState(false)
  // Drawer overlays — URL-driven so the browser back button closes them
  const tocModal = useUrlModal('toc')
  const settingsModal = useUrlModal('settings')
  const searchModal = useUrlModal('search')
  const commentsModal = useUrlModal('comments')
  const entityModal = useUrlModal('editEntity', { idKey: 'ent' })
  const tocOpen = tocModal.isOpen
  const settingsOpen = settingsModal.isOpen
  const searchOpen = searchModal.isOpen
  const commentsOpen = commentsModal.isOpen
  const [commentCount, setCommentCount] = useState(0)
  const [commentsEnabled, setCommentsEnabled] = useState(true)
  const [isFullscreen, setIsFullscreen] = useState(false)
  const [barVisible, setBarVisible] = useState(true)

  // Entity support (visible when authenticated or auth disabled)
  const [canEdit, setCanEdit] = useState(false)
  const [entities, setEntities] = useState([])
  const editingEntity = entityModal.isOpen
    ? entities.find(e => String(e.id) === entityModal.id)
    : null

  const contentRef = useRef(null)
  // In-memory cache of prefetched chapters, keyed by `${bookId}:${num}`.
  // Populated by the next-N prefetch after each successful load so that
  // tap-next/tap-prev resolves instantly without another round trip.
  const chapterCache = useRef(new Map())

  // Load book + chapter list
  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setBookError(null)
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
      } catch (e) {
        // book not found, public library off, or connection error
        if (!cancelled) setBookError(e)
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => { cancelled = true }
  }, [bookId, bookReloadTick]) // eslint-disable-line react-hooks/exhaustive-deps

  // Check if user can edit entities (authenticated or auth disabled)
  useEffect(() => {
    api.authStatus()
      .then(({ auth_required, authenticated }) => {
        setCanEdit(!auth_required || authenticated)
      })
      .catch(() => setCanEdit(false))
  }, [])

  // Load entities when authenticated (decorative for reading — warn only)
  useEffect(() => {
    if (!canEdit || !bookId) return
    api.listEntities({ book_id: parseInt(bookId), include_global: true })
      .then(res => setEntities(res.entities || []))
      .catch(e => { console.warn('Failed to load entities:', e); setEntities([]) })
  }, [canEdit, bookId])

  const reloadEntities = useCallback(() => {
    if (!canEdit || !bookId) return
    api.listEntities({ book_id: parseInt(bookId), include_global: true })
      .then(res => setEntities(res.entities || []))
      .catch(e => console.warn('Failed to reload entities:', e))
  }, [canEdit, bookId])

  // Comment count + per-book toggle (refreshed on chapter change and after drawer close)
  const refreshCommentCount = useCallback(() => {
    if (!bookId || currentNum == null) return
    const identity = loadIdentity()
    publicApi.getChapterCommentCount(bookId, currentNum, identity?.uuid)
      .then(data => {
        setCommentCount(data.count || 0)
        setCommentsEnabled(data.enabled !== false)
      })
      .catch(e => {
        console.warn('Failed to load comment count:', e)
        setCommentCount(0)
        setCommentsEnabled(true)
      })
  }, [bookId, currentNum])

  useEffect(() => {
    refreshCommentCount()
  }, [refreshCommentCount])

  const newInChapter = useMemo(
    () => entities.filter(e => e.origin_chapter === currentNum && e.book_id === parseInt(bookId)),
    [entities, currentNum, bookId]
  )

  // Load chapter content. Retries once on transient failure (iOS Safari
  // occasionally drops in-flight fetches mid-navigation, which previously
  // surfaced as a blank "Chapter " page with no error.)
  useEffect(() => {
    if (currentNum == null) return
    let cancelled = false
    const cacheKey = `${bookId}:${currentNum}`

    async function prefetchAhead() {
      // Prefetch the next two chapters into the in-memory cache so that
      // tap-next is instant. Failures are silently ignored — this is
      // strictly an optimization.
      const idx = chapters.findIndex(c => c.chapter === currentNum)
      if (idx < 0) return
      const targets = chapters.slice(idx + 1, idx + 3).map(c => c.chapter)
      const missing = targets.filter(n => !chapterCache.current.has(`${bookId}:${n}`))
      if (missing.length === 0) return
      try {
        const data = await readerApi.getChaptersBatch(bookId, missing)
        if (cancelled) return
        for (const ch of data?.chapters || []) {
          chapterCache.current.set(`${bookId}:${ch.chapter}`, ch)
        }
      } catch {
        // ignore — prefetch is best-effort
      }
    }

    async function load() {
      const cached = chapterCache.current.get(cacheKey)
      if (cached) {
        setChapter(cached)
        setChapterError(null)
        setChapterLoading(false)
        prefetchAhead()
        return
      }
      setChapterLoading(true)
      setChapterError(null)
      let lastErr = null
      for (let attempt = 0; attempt < 2; attempt++) {
        if (cancelled) return
        try {
          const data = await readerApi.getChapter(bookId, currentNum)
          if (cancelled) return
          if (!data) throw new Error('Empty response')
          setChapter(data)
          setChapterError(null)
          setChapterLoading(false)
          chapterCache.current.set(cacheKey, data)
          prefetchAhead()
          return
        } catch (e) {
          lastErr = e
          if (attempt === 0) await new Promise(r => setTimeout(r, 600))
        }
      }
      if (!cancelled) {
        setChapterError(lastErr || new Error('Failed to load chapter'))
        setChapterLoading(false)
      }
    }
    load()
    return () => { cancelled = true }
  }, [bookId, currentNum, reloadTick, chapters, readerApi])

  // Page title
  useEffect(() => {
    const parts = []
    if (chapter?.title) parts.push(chapter.title)
    else if (currentNum != null) parts.push(`Chapter ${currentNum}`)
    if (book?.title) parts.push(book.title)
    document.title = parts.length > 0 ? parts.join(' — ') : 'Reader'
    return () => { document.title = isPublic ? public_site_name : site_name }
  }, [book, chapter, currentNum, isPublic, site_name, public_site_name])

  // Save progress + update URL. Preserve the query string so drawer modal
  // state (?modal=toc etc.) survives chapter-to-chapter navigation.
  useEffect(() => {
    if (currentNum != null) {
      setProgress(prev => ({ ...prev, [bookId]: currentNum }))
      const base = libraryPrefix ? `/library/read/${bookId}` : `/read/${bookId}`
      window.history.replaceState(null, '', `${base}/${currentNum}${window.location.search}${window.location.hash}`)
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
        searchModal.open()
        return
      }
      if (e.key === 'Escape' && searchOpen) {
        searchModal.close()
        return
      }
      if (tocOpen || settingsOpen || searchOpen || commentsOpen) return
      if (e.key === 'ArrowLeft') goChapter(-1)
      if (e.key === 'ArrowRight') goChapter(1)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }) // intentionally no deps — uses latest closure

  // Auto-hide top bar on scroll — requires meaningful upward scroll to reappear
  const lastScroll = useRef(0)
  const maxScroll = useRef(0)
  const handleScroll = useCallback(() => {
    const el = contentRef.current
    if (!el) return
    const y = el.scrollTop
    if (y > lastScroll.current && y > 80) {
      // Scrolling down — hide bar and track furthest point
      setBarVisible(false)
      maxScroll.current = y
    } else if (y <= 80 || maxScroll.current - y > 50) {
      // Near top, or scrolled up 50px from the furthest-down point
      setBarVisible(true)
      maxScroll.current = 0
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

  // Footnotes: parse the bottom [n] definitions from the displayed lines and show a
  // modeless popover on click. Hooks must run before any early return below.
  const fnLines = (prefs.contentMode === 'source' && chapter?.untranslated?.length)
    ? (chapter?.untranslated || [])
    : (chapter?.content || [])
  const { map: footnotes, ids: fnIds } = useMemo(() => parseFootnotes(fnLines), [fnLines])
  const [activeFootnote, setActiveFootnote] = useState(null)
  const onFootnoteClick = useCallback((e) => {
    const ref = e.target.closest?.('.footnote-ref')
    if (!ref) return
    if (e.type === 'keydown' && e.key !== 'Enter' && e.key !== ' ') return
    e.preventDefault()
    const n = ref.getAttribute('data-fn')
    if (!footnotes[n]) return
    setActiveFootnote({ n, text: footnotes[n], rect: ref.getBoundingClientRect() })
  }, [footnotes])

  if (loading) {
    return (
      <div className={`min-h-screen ${theme.bg} flex items-center justify-center`}>
        <Loader2 size={32} className="animate-spin text-indigo-400" />
      </div>
    )
  }

  if (bookError) {
    return (
      <div className={`min-h-screen ${theme.bg}`}>
        <ErrorState
          size="page"
          className={theme.text}
          message="Couldn't load this book"
          detail={bookError.message || 'The connection may have dropped. Try again.'}
          onRetry={() => setBookReloadTick(t => t + 1)}
          buttonClassName={navBtnClass}
        />
        <div className="text-center -mt-24">
          <Link to={backPath} className="text-indigo-400 hover:underline inline-block">{isPublic ? 'Back to Library' : 'Back to Books'}</Link>
        </div>
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
  // fnLines/footnotes/fnIds are computed via hooks above the early returns.

  // Illustration URL: prefer the CDN URL baked into the payload, else fall back
  // to the API route (which itself redirects to CDN or serves local).
  const illustrationSrc = (imgId) =>
    bustUrl(
      chapter?.illustrations?.[imgId] ||
      `${isPublic ? '/api/public' : '/api'}/books/${bookId}/illustration/${imgId}`
    )

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
          <button onClick={() => tocModal.open()} className={`${barText} hover:${barTextStrong} p-1.5`} title="Table of Contents">
            <List size={20} />
          </button>
          {commentsEnabled && (
            <button onClick={() => commentsModal.open()} className={`${barText} hover:${barTextStrong} p-1.5 relative`} title="Comments">
              <MessageCircle size={20} />
              {commentCount > 0 && (
                <span className="absolute -top-1 -right-1 min-w-[16px] h-4 px-1 rounded-full bg-indigo-600 text-white text-[10px] font-medium flex items-center justify-center">
                  {commentCount > 99 ? '99+' : commentCount}
                </span>
              )}
            </button>
          )}
          <button onClick={() => searchModal.open()} className={`${barText} hover:${barTextStrong} p-1.5`} title="Search (Ctrl+F)">
            <Search size={20} />
          </button>
          <button onClick={toggleFullscreen} className={`${barText} hover:${barTextStrong} p-1.5`} title={isFullscreen ? 'Exit Full Screen' : 'Full Screen'}>
            {isFullscreen ? <Minimize size={20} /> : <Maximize size={20} />}
          </button>
          <button onClick={() => settingsModal.open()} className={`${barText} hover:${barTextStrong} p-1.5`} title="Settings">
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
        ) : chapterError || !chapter ? (
          <ErrorState
            size="page"
            className={theme.text}
            message="Couldn't load this chapter"
            detail="The connection may have dropped. Try again, or use Previous/Next to reload."
            onRetry={() => setReloadTick(t => t + 1)}
            buttonClassName={navBtnClass}
          />
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

            {/* New entities in this chapter */}
            {canEdit && newInChapter.length > 0 && (
              <details className="mb-6 text-xs">
                <summary className={`cursor-pointer select-none ${
                  isDark ? 'text-indigo-400/70 hover:text-indigo-400'
                    : prefs.theme === 'sepia' ? 'text-amber-800/60 hover:text-amber-800'
                    : 'text-indigo-500/70 hover:text-indigo-600'
                }`}>
                  {newInChapter.length} new entit{newInChapter.length === 1 ? 'y' : 'ies'} in this chapter
                </summary>
                <div className="mt-1.5 flex flex-wrap gap-1.5">
                  {newInChapter.map(ent => {
                    const colors = CATEGORY_COLORS[ent.category] || CATEGORY_COLORS.characters
                    return (
                      <span
                        key={ent.id}
                        className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded cursor-pointer
                                   hover:brightness-150 transition-all"
                        style={{ backgroundColor: colors.bg, borderBottom: `1px dashed ${colors.border}` }}
                        title={`${ent.category} — click to edit`}
                        onClick={() => entityModal.open(ent.id)}
                      >
                        <span className={isDark ? 'text-slate-400' : prefs.theme === 'sepia' ? 'text-amber-900/70' : 'text-gray-500'}>{ent.untranslated}</span>
                        <span className={isDark ? 'text-slate-600' : prefs.theme === 'sepia' ? 'text-amber-800/40' : 'text-gray-400'}>&rarr;</span>
                        <span className={isDark ? 'text-slate-300' : prefs.theme === 'sepia' ? 'text-amber-900' : 'text-gray-700'}>{ent.translation}</span>
                      </span>
                    )
                  })}
                </div>
              </details>
            )}

            {/* Chapter text */}
            <div style={contentStyle} onClick={onFootnoteClick} onKeyDown={onFootnoteClick}>
              {contentMode === 'both' && hasSource ? (
                // Interleaved: source line then translated line. Aligned 1:1, so
                // only inline Markdown (bold/italic/code/links) is rendered here —
                // block grouping would break the per-line pairing.
                translatedLines.map((line, i) => {
                  // Key illustrations off the translated line only (reconciliation
                  // guarantees every marker survives there); source/translated
                  // marker indices may differ, so using both would double-render.
                  const imgId = illustrationId(line)
                  if (imgId) {
                    return (
                      <img key={i} src={illustrationSrc(imgId)}
                        alt="" loading="lazy" className="block mx-auto my-6 max-w-full rounded" />
                    )
                  }
                  let src = sourceLines[i]
                  if (illustrationId(src)) src = ''  // hide raw source marker token
                  const isEmpty = (!line || !line.trim()) && (!src || !src.trim())
                  if (isEmpty) return <div key={i} className="h-4" />
                  return (
                    <div key={i} className="mb-4">
                      {src && src.trim() && (
                        <p className={`mb-1 text-[0.85em] ${isDark ? 'text-slate-500' : prefs.theme === 'sepia' ? 'text-amber-800/50' : 'text-gray-400'}`}>
                          {src}
                        </p>
                      )}
                      {line && line.trim() && (
                        <p className="chapter-markdown" dangerouslySetInnerHTML={{ __html: linkifyFootnotes(renderInline(markFootnoteLine(line, fnIds))) }} />
                      )}
                    </div>
                  )
                })
              ) : (
                // Single mode (source or translated): full block-level Markdown,
                // split into segments around illustration markers.
                splitSegments(markFootnoteRefs(fnLines, fnIds))
                  .map((seg, i) => seg.type === 'img' ? (
                    <img key={i} src={illustrationSrc(seg.id)}
                      alt="" loading="lazy" className="block mx-auto my-6 max-w-full rounded" />
                  ) : (
                    <div key={i} className="chapter-markdown" dangerouslySetInnerHTML={{ __html: linkifyFootnotes(renderBlock(seg.md)) }} />
                  ))
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
        onClose={tocModal.close}
        book={book}
        chapters={chapters}
        currentChapter={currentNum}
        onSelect={setCurrentNum}
        isPublic={isPublic}
        theme={prefs.theme}
      />
      <ReaderSettings
        open={settingsOpen}
        onClose={settingsModal.close}
        prefs={prefs}
        setPrefs={setPrefs}
        hasSource={hasSource}
      />
      <ReaderSearch
        open={searchOpen}
        onClose={searchModal.close}
        bookId={bookId}
        onNavigate={setCurrentNum}
        theme={prefs.theme}
        api={readerApi}
      />
      <ReaderComments
        open={commentsOpen}
        onClose={() => { commentsModal.close(); refreshCommentCount() }}
        bookId={Number(bookId)}
        chapterNumber={currentNum}
        themeMode={prefs.theme}
      />

      {/* Footnote popover (modeless, non-blocking) */}
      <FootnotePopover
        footnote={activeFootnote}
        theme={prefs.theme}
        onClose={() => setActiveFootnote(null)}
      />

      {/* Entity edit modal */}
      {editingEntity && (
        <EntityFormModal
          entity={editingEntity}
          onClose={entityModal.close}
          onSaved={() => { entityModal.close(); reloadEntities() }}
        />
      )}
    </div>
  )
}
