/**
 * ChapterEditor — split-pane translation editor for a single chapter.
 * Left: read-only Chinese source text with line highlighting, dictionary lookup,
 *       inline retranslation annotations, and entity highlighting.
 * Right: editable English translation with synchronized scrolling and
 *        entity highlighting via overlay technique.
 * Reached via /books/:bookId/chapters/:chapterNum/edit
 */
import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { useParams, useNavigate, useSearchParams, Link } from 'react-router-dom'
import { api } from '../services/api'
import { bustUrl } from '../services/cacheBust'
import { ArrowLeft, ChevronLeft, ChevronRight, Save, Loader2, Check, AlertCircle, X, Languages, CheckCircle2, Search, Pencil, Globe, Sparkles, Bold, Italic, Code, Link2, Heading, List, Quote, Minus, Eye } from 'lucide-react'
import { renderSegment, splitSegments } from '../lib/chapterMarkdown'
import { trimEmptyLines, buildMatcher } from '../lib/editorHighlights'
import DictModal from '../components/editor/DictModal'
import RetranslateModal from '../components/editor/RetranslateModal'
import PronounRepairModal from '../components/editor/PronounRepairModal'
import SelectionToolbar from '../components/editor/SelectionToolbar'
import HighlightedChineseLine from '../components/editor/HighlightedChineseLine'
import EnglishBackdrop from '../components/editor/EnglishBackdrop'
import { useSearch } from '../hooks/useSearch'
import SearchBar from '../components/SearchBar'
import EntityFormModal from '../components/EntityFormModal'
import { CATEGORY_COLORS } from '../utils/categories'
import { useUrlState, useUrlModal } from '../hooks/useUrlState'
import { useDebouncedValue } from '../hooks/useDebouncedValue'
import { useLocalStorage } from '../hooks/useLocalStorage'
import { useTransientFlag } from '../hooks/useTransientFlag'
import { useUnsavedGuard } from '../hooks/useUnsavedGuard'
import PublishMenu from '../components/PublishMenu'


// ── Main Component ───────────────────────────────────────────────────
export default function ChapterEditor() {
  const { bookId, chapterNum } = useParams()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()

  const [chapter, setChapter] = useState(null)
  const [book, setBook] = useState(null)
  const [text, setText] = useState('')
  const [untranslatedLines, setUntranslatedLines] = useState([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [saved, flashSaved, clearSaved] = useTransientFlag(3000)
  const [error, setError] = useState(null)
  const [dirty, setDirty] = useState(false)
  // Optimistic lock for concurrent saves (mirrors WriteEditor).
  const [translationDate, setTranslationDate] = useState(null)
  // Local draft recovery — while dirty, the edited text+title is autosaved to
  // localStorage (debounced); on mount a newer-than-saved draft is offered
  // back via a restore banner. Survives session expiry, crashes, and
  // accidental navigation.
  const draftKey = `chapterDraft:${bookId}:${chapterNum}`
  const [draftOffer, setDraftOffer] = useState(null) // { text, title, savedAt } or null
  const [publishedAt, setPublishedAt] = useState(null) // chapter publish state (null = draft)
  // activeLine tracked in URL (replace mode — rapid clicks shouldn't flood history)
  const [activeLine, setActiveLine] = useUrlState('line', 0, {
    serialize: String,
    deserialize: (s) => {
      const n = parseInt(s, 10)
      return Number.isFinite(n) ? n : 0
    },
  })
  const [providers, setProviders] = useState(null)
  const [entities, setEntities] = useState([])
  const [showEntities, setShowEntities] = useLocalStorage('editor.showEntities', true)
  const [showSource, setShowSource] = useLocalStorage('editor.showSource', true)
  const [showPreview, setShowPreview] = useLocalStorage('editor.showPreview', false)
  const [proofreadAt, setProofreadAt] = useState(null) // raw timestamp (or null) — tooltip needs the real date
  const [chapterList, setChapterList] = useState([])
  const [editingTitle, setEditingTitle] = useState(false)
  const [titleDraft, setTitleDraft] = useState('')
  const [editingChapterNum, setEditingChapterNum] = useState(false)
  const [chapterNumDraft, setChapterNumDraft] = useState('')
  const [renumberError, setRenumberError] = useState(null)

  // WordPress publish state
  const [wpPublishing, setWpPublishing] = useState(false)
  const [wpStatus, setWpStatus] = useState(null) // null | 'new' | 'published' | 'changed'
  const [wpMessage, flashWpMessage, clearWpMessage] = useTransientFlag(5000) // { type: 'success'|'error', text }

  // Dictionary state
  const [dictQuery, setDictQuery] = useState(null)
  const [dictData, setDictData] = useState(null)
  const [dictLoading, setDictLoading] = useState(false)
  const [dictError, setDictError] = useState(null)
  const [dictPos, setDictPos] = useState(null)
  const [selToolbar, setSelToolbar] = useState(null)

  const [pronounRepairOpen, setPronounRepairOpen] = useState(false)
  const [pronounRepairToast, flashPronounToast, clearPronounToast] = useTransientFlag(6000)
  const handlePronounRepairResult = useCallback((res) => {
    setPronounRepairOpen(false)
    flashPronounToast(res)
    // If the chapter content changed, reload to show the fix — but never
    // clobber unsaved local edits without confirmation.
    if (res?.paragraphs_changed > 0) {
      if (dirty) {
        const ok = window.confirm(
          'Pronoun repair updated this chapter on the server, but you have unsaved edits. '
          + 'Reload the repaired text and discard your local edits?'
        )
        if (!ok) return
      }
      api.getChapter(parseInt(bookId), parseInt(chapterNum))
        .then(ch => {
          if (Array.isArray(ch.content)) {
            setText(ch.content.join('\n'))
            setDirty(false)
            setChapter(ch)
            if (ch.translation_date) setTranslationDate(ch.translation_date)
          }
        })
        .catch(() => {})
    }
  }, [bookId, chapterNum, flashPronounToast, dirty])

  // Retranslation state — URL flag + local payload (text+lineIndex can't live
  // in the URL because partial selections aren't reconstructable from indices)
  const retranslateModalUrl = useUrlModal('retranslate')
  const [retranslatePayload, setRetranslatePayload] = useState(null) // { text, lineIndex }
  const retranslateModal = retranslateModalUrl.isOpen ? retranslatePayload : null
  const openRetranslateModal = useCallback((payload) => {
    setRetranslatePayload(payload)
    retranslateModalUrl.open()
  }, [retranslateModalUrl.open])
  const closeRetranslateModal = useCallback(() => {
    setRetranslatePayload(null)
    retranslateModalUrl.close()
  }, [retranslateModalUrl.close])
  const [annotations, setAnnotations] = useState({})

  // Entity edit modal — id in URL, entity object looked up from `entities`
  const entityModalUrl = useUrlModal('editEntity', { idKey: 'ent' })
  const editingEntity = entityModalUrl.isOpen
    ? entities.find(e => String(e.id) === entityModalUrl.id) || null
    : null

  // Search
  const search = useSearch()

  // Replace-all undo state
  const [undoInfo, flashUndoInfo, clearUndoInfo] = useTransientFlag(15000) // { type: 'local'|'book', prevText?, count }

  // Overlay scroll sync — kept in a ref with direct DOM writes (gutter
  // transform + backdrop scrollTop) so scroll events don't re-render the
  // whole editor.
  const overlayScrollRef = useRef(0)
  const gutterInnerRef = useRef(null)
  const backdropWrapRef = useRef(null)
  const applyOverlayScroll = useCallback((top) => {
    overlayScrollRef.current = top
    if (gutterInnerRef.current) {
      gutterInnerRef.current.style.transform = `translateY(-${top}px)`
    }
    // EnglishBackdrop's root div is the wrapper's only child — scroll it
    // directly instead of re-rendering it with a new scrollTop prop.
    const backdropEl = backdropWrapRef.current?.firstElementChild
    if (backdropEl) backdropEl.scrollTop = top
  }, [])

  const textareaRef = useRef(null)
  const chineseRef = useRef(null)
  const mirrorRef = useRef(null)
  const lineRefs = useRef([])
  const scrollSyncSource = useRef(null)
  const pendingSelection = useRef(null)
  const [lineHeights, setLineHeights] = useState([])
  const textLines = useMemo(() => text.split('\n'), [text])

  // Debounced text for the backdrop overlay (entity/search highlights)
  // The textarea itself updates instantly; the backdrop can lag slightly
  const debouncedText = useDebouncedValue(text, 200)
  const debouncedTextLines = useMemo(() => debouncedText.split('\n'), [debouncedText])

  // Build matchers from entities
  const emptyMatcher = useMemo(() => ({ lookup: new Map(), regex: null, list: [] }), [])
  const chineseMatcher = useMemo(
    () => showEntities ? buildMatcher(entities, 'untranslated') : emptyMatcher,
    [entities, showEntities, emptyMatcher]
  )
  const englishMatcher = useMemo(
    () => showEntities ? buildMatcher(entities, 'translation') : emptyMatcher,
    [entities, showEntities, emptyMatcher]
  )

  useEffect(() => {
    // Reset per-chapter state: this component instance is reused across
    // prev/next navigation. Without these, the previous chapter's dirty
    // flag re-arms the nav blocker, its retranslation annotations render on
    // the new chapter's lines, and (worst) the draft-autosave timer can
    // persist the OLD chapter's text under the NEW chapter's draft key —
    // whose "restore" would corrupt the new chapter.
    let cancelled = false
    setDraftOffer(null)
    setLoading(true)
    setError(null)
    setDirty(false)
    setAnnotations({})
    setWpStatus(null)
    setTranslationDate(null)
    clearUndoInfo()
    Promise.all([
      api.getChapter(parseInt(bookId), parseInt(chapterNum)),
      api.getBook(parseInt(bookId)),
      api.listProviders(),
      api.listEntities({ book_id: parseInt(bookId), include_global: true }),
      api.listChapters(parseInt(bookId)),
    ])
      .then(([ch, bk, prov, ents, chaps]) => {
        if (cancelled) return
        setChapter(ch)
        setBook(bk)
        setProviders(prov.providers || [])
        setEntities(ents.entities || [])
        setChapterList((chaps.chapters || []).map(c => c.chapter).sort((a, b) => a - b))
        setProofreadAt(ch.is_proofread || null)
        setPublishedAt(ch.published_at || null)
        setTranslationDate(ch.translation_date || null)
        const content = Array.isArray(ch.content) ? ch.content : []
        const loadedText = trimEmptyLines(content).join('\n')
        setText(loadedText)
        const untrans = Array.isArray(ch.untranslated) ? ch.untranslated : []
        const filtered = untrans.filter(l => !l.startsWith('#'))
        const skipped = filtered.length > 0 ? filtered.slice(1) : filtered
        setUntranslatedLines(trimEmptyLines(skipped))

        // Offer to restore an unsaved local draft, unless the stored chapter
        // was (re)translated after the draft was written (draft is stale) or
        // the draft matches what's already saved.
        try {
          const raw = localStorage.getItem(`chapterDraft:${bookId}:${chapterNum}`)
          if (raw) {
            const d = JSON.parse(raw)
            const translatedAt = ch.translation_date ? Date.parse(ch.translation_date) : NaN
            const stale = Number.isFinite(translatedAt) && d.savedAt <= translatedAt
            const differs = d.text !== loadedText || (d.title != null && d.title !== ch.title)
            if (!stale && differs && typeof d.text === 'string') {
              setDraftOffer(d)
            } else {
              localStorage.removeItem(`chapterDraft:${bookId}:${chapterNum}`)
            }
          }
        } catch { /* corrupt draft — ignore */ }
      })
      .catch(e => { if (!cancelled) setError(e.message) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [bookId, chapterNum])

  // Fetch WP publish status for this chapter
  const [wpConfigured, setWpConfigured] = useState(false)
  const [wpStoryPublished, setWpStoryPublished] = useState(false)
  useEffect(() => {
    api.wpGetSettings()
      .then(s => {
        if (!s.wp_url || !s.wp_username || !s.has_password) return
        setWpConfigured(true)
        return api.wpBookStatus(parseInt(bookId))
      })
      .then(status => {
        if (!status) return
        setWpStoryPublished(!!status.story_published)
        const ch = (status.chapters || []).find(c => c.chapter_number === parseInt(chapterNum))
        setWpStatus(ch ? ch.status : (status.story_published ? 'new' : null))
      })
      .catch(() => {})
  }, [bookId, chapterNum])

  // Open search bar from URL params (e.g. from global search modal)
  var searchParamsApplied = useRef(false)
  useEffect(function applyUrlSearch() {
    if (loading || searchParamsApplied.current) return
    var q = searchParams.get('search')
    if (!q) return
    searchParamsApplied.current = true
    var sc = searchParams.get('searchScope') || 'translated'
    var regex = searchParams.get('searchRegex') === '1'
    var bookWide = searchParams.get('searchBook') === '1'
    // Clear the URL params so they don't re-trigger
    setSearchParams({}, { replace: true })
    // Open search with the params
    search.setQuery(q)
    search.setScope(sc)
    search.setIsRegex(regex)
    search.setIsBookWide(bookWide)
    search.open()
  }, [loading])

  // Unsaved-changes guard: beforeunload + in-app navigation blocker.
  useUnsavedGuard(dirty)

  // Draft autosave — while dirty, persist edited text + title (debounced 2s)
  // so session expiry or an accidental exit can be recovered.
  const chapterTitle = chapter?.title ?? null
  useEffect(() => {
    // `!loading` guard: during chapter navigation the draftKey has already
    // changed while `text` still holds the previous chapter — arming the
    // timer in that window would save the wrong chapter's draft.
    if (!dirty || loading) return
    const timer = setTimeout(() => {
      try {
        localStorage.setItem(draftKey, JSON.stringify({
          text,
          title: chapterTitle,
          savedAt: Date.now(),
        }))
      } catch { /* quota exceeded — ignore */ }
    }, 2000)
    return () => clearTimeout(timer)
  }, [dirty, loading, text, chapterTitle, draftKey])

  const restoreDraft = useCallback(() => {
    if (!draftOffer) return
    setText(draftOffer.text)
    if (draftOffer.title != null) {
      setChapter(prev => (prev ? { ...prev, title: draftOffer.title } : prev))
    }
    setDirty(true)
    clearSaved()
    setDraftOffer(null)
  }, [draftOffer, clearSaved])

  const discardDraft = useCallback(() => {
    localStorage.removeItem(draftKey)
    setDraftOffer(null)
  }, [draftKey])

  // Global keyboard shortcut for search (Ctrl+F / Ctrl+H)
  useEffect(() => {
    const handler = (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'f') {
        e.preventDefault()
        search.open()
      }
      if ((e.ctrlKey || e.metaKey) && e.key === 'h') {
        e.preventDefault()
        search.open({ focusReplace: true })
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [search])

  // Measure wrapped line heights for the gutter (debounced to avoid per-keystroke DOM thrashing)
  const measureTimerRef = useRef(null)
  const textLinesRef = useRef(textLines)
  textLinesRef.current = textLines

  const measureLineHeightsNow = useCallback(() => {
    const mirror = mirrorRef.current
    const ta = textareaRef.current
    if (!mirror || !ta) return
    // Match the textarea's real wrapping geometry so the mirror wraps at exactly
    // the same points. clientWidth already excludes the vertical scrollbar; the
    // text wraps inside clientWidth minus the horizontal padding, and the wrap
    // rules (font, letter-spacing, tab-size, word-break) must match too — otherwise
    // long wrapped lines measure short and the gutter numbers drift.
    const cs = getComputedStyle(ta)
    const padL = parseFloat(cs.paddingLeft) || 0
    const padR = parseFloat(cs.paddingRight) || 0
    mirror.style.width = (ta.clientWidth - padL - padR) + 'px'
    mirror.style.font = cs.font
    mirror.style.letterSpacing = cs.letterSpacing
    mirror.style.lineHeight = cs.lineHeight
    mirror.style.tabSize = cs.tabSize
    mirror.style.whiteSpace = cs.whiteSpace
    mirror.style.overflowWrap = cs.overflowWrap
    mirror.style.wordBreak = cs.wordBreak
    const lines = textLinesRef.current
    // Batch DOM writes then reads: append every line's node first, measure
    // them all, then clear \u2014 one reflow total instead of one per line.
    mirror.textContent = ''
    const nodes = []
    const frag = document.createDocumentFragment()
    for (const line of lines) {
      const node = document.createElement('div')
      node.textContent = line || '\u00A0'
      frag.appendChild(node)
      nodes.push(node)
    }
    mirror.appendChild(frag)
    const heights = nodes.map(node => node.offsetHeight)
    mirror.textContent = ''
    setLineHeights(heights)
  }, []) // stable — reads textLines from ref

  const measureLineHeights = useCallback(() => {
    clearTimeout(measureTimerRef.current)
    measureTimerRef.current = setTimeout(measureLineHeightsNow, 300)
  }, [measureLineHeightsNow])

  // Resize: remeasure immediately
  useEffect(() => {
    window.addEventListener('resize', measureLineHeightsNow)
    return () => {
      window.removeEventListener('resize', measureLineHeightsNow)
      clearTimeout(measureTimerRef.current)
    }
  }, [measureLineHeightsNow])

  // Text changes: debounced remeasure (immediate on first non-empty text)
  const initialMeasureDone = useRef(false)
  useEffect(() => {
    if (!initialMeasureDone.current && text) {
      initialMeasureDone.current = true
      measureLineHeightsNow()
    } else {
      measureLineHeights()
    }
  }, [text, measureLineHeightsNow, measureLineHeights])

  // Recompute chapter-level search matches when inputs change
  const searchDebounceRef = useRef(null)
  const prevIsBookWide = useRef(search.isBookWide)

  useEffect(() => {
    if (!search.isOpen || !search.query) {
      search.updateChapterMatches('', '', [], 'both', false)
      return
    }

    // Always compute local chapter matches instantly
    search.updateChapterMatches(search.query, text, untranslatedLines, search.scope, search.isRegex)

    // Book-wide search: fire immediately when toggling on, debounce for query changes
    if (search.isBookWide) {
      const justToggledOn = !prevIsBookWide.current
      clearTimeout(searchDebounceRef.current)
      if (justToggledOn) {
        // Immediate search when toggling book-wide on
        search.searchBook(bookId, search.query, search.scope, search.isRegex)
      } else {
        searchDebounceRef.current = setTimeout(() => {
          search.searchBook(bookId, search.query, search.scope, search.isRegex)
        }, 300)
      }
    }

    prevIsBookWide.current = search.isBookWide
    return () => clearTimeout(searchDebounceRef.current)
  }, [search.isOpen, search.query, search.scope, search.isRegex, search.isBookWide, text, untranslatedLines, bookId])

  // After navigating to a new chapter in book-wide mode, sync the book index
  useEffect(() => {
    if (search.isOpen && search.isBookWide && search.bookMatchOrder.length > 0) {
      search.syncBookIndexToChapter(parseInt(chapterNum))
    }
  }, [chapterNum, search.bookMatchOrder])

  // Search handlers
  const handleSearchNext = useCallback(() => {
    const result = search.nextMatch(parseInt(chapterNum))
    if (result?.navigateTo) goToChapter(result.navigateTo)
  }, [search, chapterNum])

  const handleSearchPrev = useCallback(() => {
    const result = search.prevMatch(parseInt(chapterNum))
    if (result?.navigateTo) goToChapter(result.navigateTo)
  }, [search, chapterNum])

  const handleSearchReplace = useCallback(() => {
    const match = search.activeMatch
    if (!match || match.field !== 'translated') return
    // Refuse to apply another chapter's offsets to this buffer.
    if (match.chapterNum != null && match.chapterNum !== parseInt(chapterNum)) return
    const newText = search.replaceCurrentMatch(text, match, parseInt(chapterNum))
    if (newText === text) return
    setText(newText)
    setDirty(true)
    clearSaved()
    // Advance to next match
    setTimeout(() => handleSearchNext(), 10)
  }, [search, text, handleSearchNext, clearSaved, chapterNum])

  // Returns true on success, false on failure (mirrors WriteEditor's doSave)
  // so callers like publish flows can abort instead of proceeding with stale
  // content. `overrideLines` lets callers save content that isn't in state
  // yet (e.g. Replace All applies the replacement and saves in one step).
  // `overrideTitle` lets Replace All also rewrite the current chapter title
  // (server-side replace already does titles for other chapters).
  const handleSave = async (overrideLines = null, overrideTitle = null) => {
    setSaving(true)
    setError(null)
    try {
      const payload = { content: Array.isArray(overrideLines) ? overrideLines : textLines }
      if (overrideTitle != null) {
        payload.title = overrideTitle
      } else if (chapter && chapter.title !== undefined) {
        payload.title = chapter.title
      }
      if (translationDate) {
        payload.expected_translation_date = translationDate
      }
      const res = await api.updateChapter(parseInt(bookId), parseInt(chapterNum), payload)
      if (res?.translation_date) setTranslationDate(res.translation_date)
      if (overrideTitle != null && chapter) {
        setChapter({ ...chapter, title: overrideTitle })
      }
      flashSaved()
      setDirty(false)
      localStorage.removeItem(draftKey)  // saved — draft no longer needed
      setDraftOffer(null)
      return true
    } catch (e) {
      if (e.status === 409) {
        setError(
          'Chapter changed on the server (another tab or replace). '
          + 'Reload the page to pick up the latest text before saving again.'
        )
      } else {
        setError(e.message)
      }
      return false
    } finally {
      setSaving(false)
    }
  }

  const handleUndo = useCallback(async function doUndo() {
    if (!undoInfo) return
    if (undoInfo.type === 'local') {
      // Restore local text
      if (undoInfo.prevText != null) {
        setText(undoInfo.prevText)
        setDirty(true)
        clearSaved()
      }
    } else if (undoInfo.type === 'book') {
      // Undo server-side replacements
      try {
        await api.undoReplace(parseInt(bookId))
      } catch (err) {
        setError(err.message)
      }
      // Also restore local text for the current chapter
      if (undoInfo.prevText != null) {
        setText(undoInfo.prevText)
        setDirty(true)
        clearSaved()
      }
      search.searchBook(bookId, search.query, search.scope, search.isRegex)
    }
    clearUndoInfo()
  }, [undoInfo, bookId, search, clearUndoInfo, clearSaved])

  const handleSearchReplaceAll = useCallback(async function doReplaceAll() {
    if (!search.query) return
    var prevText = text
    var replaceable = search.replaceableBookCount || 0
    if (search.isBookWide) {
      if (replaceable === 0) return
      if (!confirm('Replace all ' + replaceable + ' translated match'
        + (replaceable === 1 ? '' : 'es') + ' across the entire book?')) return
      // Replace in current chapter body + title locally (titles are a separate
      // column — the API path rewrites them for other chapters).
      var newText = search.replaceAllInChapter(text)
      var curTitle = chapter?.title || ''
      var newTitle = search.replaceInTitle(curTitle)
      if (newText !== text) {
        setText(newText)
        setDirty(true)
        clearSaved()
      }
      // Persist the current chapter before sweeping the rest of the book —
      // the API call below rewrites every other chapter instantly, so if the
      // current chapter's replacements stayed only in local state a later
      // discard would leave the book half-swept. Abort on save failure
      // (handleSave surfaces the error toast).
      if (newText !== text || newTitle !== curTitle || dirty) {
        var savedOk = await handleSave(
          newText.split('\n'),
          newTitle !== curTitle ? newTitle : null,
        )
        if (!savedOk) return
      }
      // Replace in other chapters via API (include_titles defaults true)
      var otherChapters = (search.bookResults?.results || [])
        .map(function getNum(r) { return r.chapter_number })
        .filter(function notCurrent(n) { return n !== parseInt(chapterNum) })
      if (otherChapters.length > 0) {
        try {
          await api.replaceInBook(parseInt(bookId), {
            query: search.query,
            replacement: search.replaceText,
            chapter_numbers: otherChapters,
            is_regex: search.isRegex,
          })
        } catch (err) {
          setError(err.message)
          return
        }
      }
      search.searchBook(bookId, search.query, search.scope, search.isRegex)
      flashUndoInfo({ type: 'book', prevText: prevText, count: replaceable })
    } else {
      var replaced = search.replaceAllInChapter(text)
      if (replaced !== text) {
        setText(replaced)
        setDirty(true)
        clearSaved()
        var chCount = search.chapterMatches.filter(function onlyTrans(m) { return m.field === 'translated' }).length
        flashUndoInfo({ type: 'local', prevText: prevText, count: chCount })
      }
    }
  }, [search, text, dirty, bookId, chapterNum, chapter, flashUndoInfo, clearSaved, handleSave])

  const handleSearchClose = useCallback(() => {
    search.close()
  }, [search])

  // In book-wide mode, activeMatch may point to a different chapter.
  // Only use it for highlighting if it belongs to the current chapter.
  const currentChapterActiveMatch = useMemo(() => {
    const match = search.activeMatch
    if (!match) return null
    if (match.chapterNum != null && match.chapterNum !== parseInt(chapterNum)) return null
    return match
  }, [search.activeMatch, chapterNum])

  // Expose to SearchBar so Replace is disabled when the active match is
  // in another chapter or in source text.
  search.canReplaceActive = !!(
    currentChapterActiveMatch && currentChapterActiveMatch.field === 'translated'
  )

  // Scroll to active search match (only if it's in the current chapter)
  useEffect(() => {
    if (!currentChapterActiveMatch || !search.isOpen) return
    const match = currentChapterActiveMatch

    if (match.field === 'untranslated') {
      const lineEl = lineRefs.current[match.line]
      if (lineEl) lineEl.scrollIntoView({ block: 'center', behavior: 'smooth' })
    } else if (match.field === 'translated') {
      const ta = textareaRef.current
      if (!ta) return
      const lineHeight = ta.scrollHeight / Math.max(textLines.length, 1)
      const targetScroll = match.line * lineHeight - ta.clientHeight / 2
      ta.scrollTop = Math.max(0, targetScroll)
      applyOverlayScroll(ta.scrollTop)
    }
  }, [currentChapterActiveMatch, search.isOpen, applyOverlayScroll])

  // Compute search matches for highlighting in the current chapter view
  const chineseSearchMatches = useMemo(() => {
    if (!search.isOpen || !search.query) return {}
    const byLine = {}
    for (const m of search.chapterMatches) {
      if (m.field !== 'untranslated') continue
      if (!byLine[m.line]) byLine[m.line] = []
      byLine[m.line].push(m)
    }
    return byLine
  }, [search.isOpen, search.query, search.chapterMatches])

  const translatedSearchMatches = useMemo(() => {
    if (!search.isOpen || !search.query) return {}
    const byLine = {}
    for (const m of search.chapterMatches) {
      if (m.field !== 'translated') continue
      if (!byLine[m.line]) byLine[m.line] = []
      byLine[m.line].push(m)
    }
    return byLine
  }, [search.isOpen, search.query, search.chapterMatches])

  const handleChange = (e) => {
    setText(e.target.value)
    setDirty(true)
    clearSaved()
  }

  // Wrap/prefix the current textarea selection with Markdown syntax.
  const applyFormat = useCallback((kind) => {
    const el = textareaRef.current
    if (!el) return
    const value = el.value
    const start = el.selectionStart
    const end = el.selectionEnd
    const selected = value.slice(start, end)
    let newValue, selStart, selEnd

    const wrap = (marker, placeholder) => {
      const inner = selected || placeholder
      newValue = value.slice(0, start) + marker + inner + marker + value.slice(end)
      selStart = start + marker.length
      selEnd = selStart + inner.length
    }
    const linePrefix = (prefix) => {
      const ls = value.lastIndexOf('\n', start - 1) + 1
      newValue = value.slice(0, ls) + prefix + value.slice(ls)
      selStart = start + prefix.length
      selEnd = end + prefix.length
    }

    switch (kind) {
      case 'bold': wrap('**', 'bold text'); break
      case 'italic': wrap('*', 'italic text'); break
      case 'code': wrap('`', 'code'); break
      case 'link': {
        const inner = selected || 'text'
        newValue = value.slice(0, start) + `[${inner}](url)` + value.slice(end)
        selStart = start + inner.length + 3   // position of 'url'
        selEnd = selStart + 3
        break
      }
      case 'h2': linePrefix('## '); break
      case 'quote': linePrefix('> '); break
      case 'list': linePrefix('- '); break
      case 'hr': {
        const before = value.slice(0, start)
        const lead = before && !before.endsWith('\n\n') ? '\n\n' : ''
        const insert = `${lead}---\n\n`
        newValue = before + insert + value.slice(end)
        selStart = selEnd = before.length + insert.length
        break
      }
      default: return
    }

    setText(newValue)
    setDirty(true)
    clearSaved()
    requestAnimationFrame(() => {
      el.focus()
      el.setSelectionRange(selStart, selEnd)
    })
  }, [clearSaved])

  // Illustration URL for the preview pane — prefer the CDN URL on the payload,
  // else the admin serve route (mirrors Reader.illustrationSrc).
  const previewIllustrationSrc = (id) =>
    bustUrl(chapter?.illustrations?.[id] || `/api/books/${bookId}/illustration/${id}`)

  const toggleProofread = async () => {
    try {
      const res = await api.setProofread(parseInt(bookId), parseInt(chapterNum), !proofreadAt)
      setProofreadAt(res.is_proofread || null)
    } catch (e) {
      setError(e.message)
    }
  }

  const handleWpPublish = async () => {
    if (dirty) {
      if (!confirm('You have unsaved changes. Save and publish?')) return
      if (!(await handleSave())) return  // save failed — don't push stale content
    }
    setWpPublishing(true)
    clearWpMessage()
    try {
      const res = await api.wpPublishChapter(parseInt(bookId), parseInt(chapterNum))
      const actionText = res.action === 'created' ? 'Published' : res.action === 'updated' ? 'Updated' : 'Already up to date'
      flashWpMessage({ type: 'success', text: actionText })
      setWpStatus('published')
    } catch (e) {
      flashWpMessage({ type: 'error', text: e.message }, 8000)
    } finally {
      setWpPublishing(false)
    }
  }

  const handleKeyDown = (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'f') {
      e.preventDefault()
      search.open()
      return
    }
    if ((e.ctrlKey || e.metaKey) && e.key === 'h') {
      e.preventDefault()
      search.open({ focusReplace: true })
      return
    }
    if ((e.ctrlKey || e.metaKey) && e.key === 's') {
      e.preventDefault()
      handleSave()
    }
    if (e.key === 'Tab') {
      e.preventDefault()
      const start = e.target.selectionStart
      const end = e.target.selectionEnd
      const newText = text.substring(0, start) + '  ' + text.substring(end)
      setText(newText)
      setDirty(true)
      requestAnimationFrame(() => {
        if (textareaRef.current) {
          textareaRef.current.selectionStart = start + 2
          textareaRef.current.selectionEnd = start + 2
        }
      })
    }
  }

  const textRef = useRef(text)
  textRef.current = text

  const updateActiveLine = useCallback(() => {
    if (!textareaRef.current) return
    const pos = textareaRef.current.selectionStart
    const lineNum = textRef.current.substring(0, pos).split('\n').length - 1
    setActiveLine(lineNum)
  }, [setActiveLine])

  const handleTextareaScroll = useCallback(() => {
    const ta = textareaRef.current
    if (ta) applyOverlayScroll(ta.scrollTop)

    if (scrollSyncSource.current === 'chinese') return
    scrollSyncSource.current = 'english'
    const ch = chineseRef.current
    if (!ta || !ch) return
    const scrollRatio = ta.scrollTop / (ta.scrollHeight - ta.clientHeight || 1)
    ch.scrollTop = scrollRatio * (ch.scrollHeight - ch.clientHeight || 1)
    requestAnimationFrame(() => { scrollSyncSource.current = null })
  }, [applyOverlayScroll])

  const handleChineseScroll = useCallback(() => {
    if (scrollSyncSource.current === 'english') return
    scrollSyncSource.current = 'chinese'
    const ta = textareaRef.current
    const ch = chineseRef.current
    if (!ta || !ch) return
    const scrollRatio = ch.scrollTop / (ch.scrollHeight - ch.clientHeight || 1)
    ta.scrollTop = scrollRatio * (ta.scrollHeight - ta.clientHeight || 1)
    applyOverlayScroll(ta.scrollTop)
    requestAnimationFrame(() => { scrollSyncSource.current = null })
  }, [applyOverlayScroll])

  // ── Dictionary lookup ────────────────────────────────────────────
  const doLookup = useCallback(async (queryText, pos) => {
    const q = queryText.trim()
    if (!q) return
    setDictQuery(q)
    setDictData(null)
    setDictError(null)
    setDictLoading(true)
    setDictPos(pos)
    setSelToolbar(null)
    try {
      const result = await api.dictLookup(q)
      setDictData(result)
    } catch (e) {
      setDictError(e.message)
    } finally {
      setDictLoading(false)
    }
  }, [])

  const closeDictModal = useCallback(() => {
    setDictQuery(null)
    setDictData(null)
    setDictError(null)
    setDictPos(null)
  }, [])

  // ── Chinese panel selection handling ──────────────────────────────
  const getLineIndexFromSelection = useCallback(() => {
    const sel = window.getSelection()
    if (!sel?.rangeCount) return null
    let node = sel.anchorNode
    while (node && node !== chineseRef.current) {
      if (node.dataset?.lineIdx !== undefined) return parseInt(node.dataset.lineIdx)
      node = node.parentElement
    }
    return null
  }, [])

  const handleChineseMouseUp = useCallback(() => {
    const sel = window.getSelection()
    const selectedText = sel?.toString().trim()
    if (!selectedText) {
      setSelToolbar(null)
      return
    }
    if (!/[\u4e00-\u9fff\u3400-\u4dbf]/.test(selectedText)) {
      setSelToolbar(null)
      return
    }
    const range = sel.getRangeAt(0)
    const rect = range.getBoundingClientRect()
    const lineIdx = getLineIndexFromSelection()
    pendingSelection.current = { text: selectedText, x: rect.left, y: rect.bottom, lineIndex: lineIdx }
    setSelToolbar({
      x: rect.left + rect.width / 2 - 80,
      y: rect.top,
    })
  }, [getLineIndexFromSelection])

  const handleChineseDblClick = useCallback(() => {
    const sel = window.getSelection()
    const selectedText = sel?.toString().trim()
    if (!selectedText || !/[\u4e00-\u9fff\u3400-\u4dbf]/.test(selectedText)) return
    const rect = sel.getRangeAt(0).getBoundingClientRect()
    doLookup(selectedText, { x: rect.left, y: rect.bottom })
  }, [doLookup])

  const handleToolbarLookup = useCallback(() => {
    if (!pendingSelection.current) return
    const { text: selText, x, y } = pendingSelection.current
    doLookup(selText, { x, y })
  }, [doLookup])

  // ── Retranslation ────────────────────────────────────────────────
  const handleToolbarRetranslate = useCallback(() => {
    if (!pendingSelection.current) return
    const { text: selText, lineIndex } = pendingSelection.current
    setSelToolbar(null)
    openRetranslateModal({ text: selText, lineIndex })
  }, [openRetranslateModal])

  const handleRetranslateLine = useCallback((lineIndex) => {
    const line = untranslatedLines[lineIndex]
    if (!line) return
    openRetranslateModal({ text: line, lineIndex })
  }, [openRetranslateModal, untranslatedLines])

  const handleRetranslateResult = useCallback((chineseText, translation, lineIndex) => {
    if (lineIndex != null) {
      setAnnotations(prev => ({ ...prev, [lineIndex]: translation }))
    }
  }, [])

  // ── Entity editing ───────────────────────────────────────────────
  const handleEntityClick = useCallback((matcherEntity) => {
    // Find the full entity record (with id) from our loaded entities list
    const full = entities.find(e =>
      e.untranslated === matcherEntity.untranslated && e.category === matcherEntity.category
    )
    if (full) entityModalUrl.open(full.id)
  }, [entityModalUrl.open, entities])

  const handleEntitySaved = useCallback(() => {
    entityModalUrl.close()
    // Reload entities to reflect changes
    api.listEntities({ book_id: parseInt(bookId), include_global: true })
      .then(res => setEntities(res.entities || []))
      .catch(() => {})
  }, [entityModalUrl.close, bookId])

  const lineCount = textLines.length
  const wordCount = text.trim() ? text.trim().split(/\s+/).length : 0
  const entityCount = entities.length

  // Prev / next chapter navigation
  const chapterIdx = chapterList.indexOf(parseInt(chapterNum))
  const prevChapter = chapterIdx > 0 ? chapterList[chapterIdx - 1] : null
  const nextChapter = chapterIdx >= 0 && chapterIdx < chapterList.length - 1 ? chapterList[chapterIdx + 1] : null

  // Unsaved-changes confirmation is handled by the useBlocker guard above.
  const goToChapter = (num) => {
    navigate(`/books/${bookId}/chapters/${num}/edit`)
  }

  const commitChapterNumber = async () => {
    const parsed = parseInt(chapterNumDraft, 10)
    const current = parseInt(chapterNum, 10)
    if (!Number.isFinite(parsed) || parsed < 1) {
      setRenumberError('Chapter number must be a positive integer.')
      return
    }
    if (parsed === current) {
      setEditingChapterNum(false)
      setRenumberError(null)
      return
    }
    if (dirty) {
      setRenumberError('Save your changes before renumbering.')
      return
    }
    try {
      await api.renumberChapter(parseInt(bookId), current, parsed)
      setEditingChapterNum(false)
      setRenumberError(null)
      navigate(`/books/${bookId}/chapters/${parsed}/edit`)
    } catch (e) {
      setRenumberError(e.message || 'Failed to renumber chapter.')
    }
  }

  // Entities first discovered in this chapter
  const chapterNum_int = parseInt(chapterNum)
  const newInChapter = useMemo(
    () => entities.filter(e => e.origin_chapter === chapterNum_int && e.book_id === parseInt(bookId)),
    [entities, chapterNum_int, bookId]
  )

  if (loading) {
    return (
      <div className="h-full flex items-center justify-center text-slate-400">
        <Loader2 size={18} className="animate-spin mr-2" /> Loading chapter...
      </div>
    )
  }

  if (error && !chapter) {
    return (
      <div className="h-full flex items-center justify-center">
        <div className="card p-6 text-center max-w-sm">
          <AlertCircle size={24} className="text-rose-400 mx-auto mb-3" />
          <p className="text-slate-300 text-sm">{error}</p>
          <Link to="/books" className="btn-secondary mt-4 inline-block text-sm">Back to Books</Link>
        </div>
      </div>
    )
  }

  const hasSource = untranslatedLines.length > 0

  return (
    <div className="h-full flex flex-col">
      {/* Toolbar */}
      <div className="flex items-center gap-2 md:gap-3 px-3 md:px-5 py-2 md:py-3 border-b border-slate-800 bg-slate-900/50 shrink-0 flex-wrap">
        <button
          className="btn-ghost p-1.5"
          onClick={() => navigate('/books')}
        >
          <ArrowLeft size={16} />
        </button>

        <button
          className="btn-ghost p-1.5"
          onClick={() => goToChapter(prevChapter)}
          disabled={prevChapter == null}
          title={prevChapter != null ? `Chapter ${prevChapter}` : 'No previous chapter'}
        >
          <ChevronLeft size={16} />
        </button>
        <button
          className="btn-ghost p-1.5"
          onClick={() => goToChapter(nextChapter)}
          disabled={nextChapter == null}
          title={nextChapter != null ? `Chapter ${nextChapter}` : 'No next chapter'}
        >
          <ChevronRight size={16} />
        </button>

        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium text-slate-200 truncate flex items-center gap-1">
            <span className="truncate">{book?.title} — Chapter</span>
            {editingChapterNum ? (
              <input
                autoFocus
                type="number"
                min="1"
                className="px-1.5 py-0.5 bg-slate-800 border border-slate-600 rounded text-sm text-slate-200 outline-none focus:border-sky-500 w-20"
                value={chapterNumDraft}
                onChange={e => { setChapterNumDraft(e.target.value); setRenumberError(null) }}
                onKeyDown={e => {
                  if (e.key === 'Enter') {
                    e.preventDefault()
                    commitChapterNumber()
                  } else if (e.key === 'Escape') {
                    setEditingChapterNum(false)
                    setRenumberError(null)
                  }
                }}
                onBlur={() => commitChapterNumber()}
              />
            ) : (
              <span
                className="cursor-pointer hover:text-sky-300 inline-flex items-center gap-1 group"
                onClick={() => { setChapterNumDraft(String(chapterNum)); setEditingChapterNum(true); setRenumberError(null) }}
                title="Click to change chapter number"
              >
                {chapterNum}
                <Pencil size={11} className="opacity-0 group-hover:opacity-100 transition-opacity" />
              </span>
            )}
            {renumberError && (
              <span className="text-rose-400 text-xs ml-2 font-normal">{renumberError}</span>
            )}
          </div>
          <div className="text-xs text-slate-500 mt-0.5 flex items-center gap-1">
            {editingTitle ? (
              <input
                autoFocus
                className="px-1.5 py-0.5 bg-slate-800 border border-slate-600 rounded text-xs text-slate-200 outline-none focus:border-sky-500 w-56"
                value={titleDraft}
                onChange={e => setTitleDraft(e.target.value)}
                onKeyDown={e => {
                  if (e.key === 'Enter') {
                    const newTitle = titleDraft.trim() || `Chapter ${chapterNum}`
                    setChapter(prev => ({ ...prev, title: newTitle }))
                    setEditingTitle(false)
                    setDirty(true)
                  } else if (e.key === 'Escape') {
                    setEditingTitle(false)
                  }
                }}
                onBlur={() => {
                  const newTitle = titleDraft.trim() || `Chapter ${chapterNum}`
                  setChapter(prev => ({ ...prev, title: newTitle }))
                  setEditingTitle(false)
                  setDirty(true)
                }}
              />
            ) : (
              <span
                className="cursor-pointer hover:text-slate-300 inline-flex items-center gap-1 group"
                onClick={() => { setTitleDraft(chapter?.title || ''); setEditingTitle(true) }}
                title="Click to edit chapter title"
              >
                {chapter?.title && chapter.title !== `Chapter ${chapterNum}` ? `"${chapter.title}"` : <span className="text-slate-600 italic">No title</span>}
                <Pencil size={11} className="opacity-0 group-hover:opacity-100 transition-opacity" />
              </span>
            )}
            <span className="text-slate-600 mx-1">·</span>
            {lineCount.toLocaleString()} lines · {wordCount.toLocaleString()} words
            {chapter?.model && <span> · {chapter.model}</span>}
            {dirty && <span className="text-amber-500 ml-2">· Unsaved changes</span>}
          </div>
        </div>

        {error && (
          <span className="text-rose-400 text-xs">{error}</span>
        )}

        {saved && (
          <span className="text-emerald-400 text-xs flex items-center gap-1">
            <Check size={12} /> Saved
          </span>
        )}

        {/* Markdown formatting toolbar */}
        <div className="flex items-center gap-0.5 rounded border border-slate-700 px-0.5 py-0.5">
          {[
            { kind: 'bold', Icon: Bold, title: 'Bold (**)' },
            { kind: 'italic', Icon: Italic, title: 'Italic (*)' },
            { kind: 'code', Icon: Code, title: 'Inline code (`)' },
            { kind: 'link', Icon: Link2, title: 'Link' },
            { kind: 'h2', Icon: Heading, title: 'Heading (##)' },
            { kind: 'list', Icon: List, title: 'List (-)' },
            { kind: 'quote', Icon: Quote, title: 'Blockquote (>)' },
            { kind: 'hr', Icon: Minus, title: 'Scene break (---)' },
          ].map(({ kind, Icon, title }) => (
            <button
              key={kind}
              type="button"
              className="p-1 rounded text-slate-400 hover:text-slate-100 hover:bg-slate-700 transition-colors"
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => applyFormat(kind)}
              title={title}
            >
              <Icon size={13} />
            </button>
          ))}
        </div>

        {/* Preview toggle */}
        <button
          className={`text-xs px-2 py-1 rounded border transition-colors flex items-center gap-1 ${
            showPreview
              ? 'border-indigo-500/50 bg-indigo-500/10 text-indigo-300'
              : 'border-slate-700 text-slate-500 hover:text-slate-400'
          }`}
          onClick={() => setShowPreview(!showPreview)}
          title="Toggle rendered Markdown preview"
        >
          <Eye size={12} />
          {showPreview ? 'Preview' : 'Preview off'}
        </button>

        {/* Source panel toggle — greyed out for original works (no source text) */}
        <button
          disabled={!hasSource}
          className={`text-xs px-2 py-1 rounded border transition-colors flex items-center gap-1 ${
            !hasSource
              ? 'border-slate-800 text-slate-600 opacity-50 cursor-not-allowed'
              : showSource
                ? 'border-sky-500/50 bg-sky-500/10 text-sky-300'
                : 'border-slate-700 text-slate-500 hover:text-slate-400'
          }`}
          onClick={() => hasSource && setShowSource(!showSource)}
          title={!hasSource ? 'No source text for original works' : showSource ? 'Hide Chinese source' : 'Show Chinese source'}
        >
          <Languages size={12} />
          {hasSource && !showSource ? 'Source off' : 'Source'}
        </button>

        {/* Entity highlight toggle */}
        {entityCount > 0 && (
          <button
            className={`text-xs px-2 py-1 rounded border transition-colors ${
              showEntities
                ? 'border-indigo-500/50 bg-indigo-500/10 text-indigo-300'
                : 'border-slate-700 text-slate-500 hover:text-slate-400'
            }`}
            onClick={() => setShowEntities(!showEntities)}
            title={`${entityCount} entities loaded`}
          >
            {showEntities ? `Entities (${entityCount})` : 'Entities off'}
          </button>
        )}

        <button
          className={`text-xs px-2 py-1 rounded border transition-colors flex items-center gap-1 ${
            proofreadAt
              ? 'border-emerald-500/50 bg-emerald-500/10 text-emerald-300'
              : 'border-slate-700 text-slate-500 hover:text-slate-400'
          }`}
          onClick={toggleProofread}
          title={proofreadAt ? `Proofread ${new Date(proofreadAt).toLocaleDateString()}` : 'Mark as proofread'}
        >
          <CheckCircle2 size={12} />
          {proofreadAt ? 'Proofread' : 'Not proofread'}
        </button>

        <button
          className={`text-xs px-2 py-1 rounded border transition-colors flex items-center gap-1 ${
            search.isOpen
              ? 'border-indigo-500/50 bg-indigo-500/20 text-indigo-300'
              : 'border-slate-700 text-slate-500 hover:text-slate-400'
          }`}
          onClick={() => search.isOpen ? search.close() : search.open()}
          title="Search & Replace (Ctrl+F)"
        >
          <Search size={12} />
          Find
        </button>

        <button
          className="text-xs px-2 py-1 rounded border border-slate-700 text-slate-500 hover:text-slate-400 hover:border-emerald-500/40 hover:text-emerald-300 transition-colors flex items-center gap-1"
          onClick={() => setPronounRepairOpen(true)}
          title="Repair wrong-gender pronouns for a character in this chapter"
        >
          <Sparkles size={12} />
          Repair pronouns
        </button>

        <PublishMenu
          bookId={parseInt(bookId)}
          chapterNum={parseInt(chapterNum)}
          publishedAt={publishedAt}
          onChanged={setPublishedAt}
          beforePublish={async () => (dirty ? await handleSave() : true)}
        />

        {wpConfigured && (
          <button
            className={`text-xs px-2 py-1 rounded border transition-colors flex items-center gap-1 ${
              !wpStoryPublished
                ? 'border-slate-700 text-slate-600 cursor-not-allowed'
                : wpStatus === 'published'
                ? 'border-emerald-500/50 bg-emerald-500/10 text-emerald-300'
                : wpStatus === 'changed'
                ? 'border-amber-500/50 bg-amber-500/10 text-amber-300'
                : 'border-slate-700 text-slate-500 hover:text-slate-400'
            }`}
            onClick={wpStoryPublished ? handleWpPublish : undefined}
            disabled={wpPublishing || !wpStoryPublished}
            title={!wpStoryPublished ? 'Publish the book from the Books page first' : wpStatus === 'new' ? 'Publish this chapter to WordPress' : wpStatus === 'changed' ? 'Update this chapter on WordPress (content changed)' : 'Re-publish this chapter to WordPress'}
          >
            {wpPublishing
              ? <Loader2 size={12} className="animate-spin" />
              : <Globe size={12} />}
            {!wpStoryPublished ? 'WP' : wpStatus === 'new' ? 'Publish' : wpStatus === 'changed' ? 'Update WP' : 'Published'}
          </button>
        )}

        {wpMessage && (
          <span className={`text-xs flex items-center gap-1 ${wpMessage.type === 'success' ? 'text-emerald-400' : 'text-rose-400'}`}>
            {wpMessage.type === 'success' ? <Check size={12} /> : <AlertCircle size={12} />}
            {wpMessage.text}
          </span>
        )}

        <button
          className="btn-primary flex items-center gap-1.5"
          onClick={handleSave}
          disabled={saving || !dirty}
        >
          {saving
            ? <Loader2 size={13} className="animate-spin" />
            : <Save size={13} />}
          Save
          <span className="text-indigo-300 text-xs ml-0.5">&#8984;S</span>
        </button>
      </div>

      {/* Unsaved-draft restore banner */}
      {draftOffer && (
        <div className="flex items-center gap-3 px-5 py-2 bg-amber-950/40 border-b border-amber-900/60 text-xs shrink-0">
          <AlertCircle size={14} className="text-amber-400 shrink-0" />
          <span className="text-amber-300 flex-1 min-w-0 truncate">
            Unsaved draft from {new Date(draftOffer.savedAt).toLocaleString()} found for this chapter.
          </span>
          <button
            className="px-2.5 py-1 rounded border border-amber-700 text-amber-200 hover:bg-amber-900/50 shrink-0"
            onClick={restoreDraft}
          >
            Restore
          </button>
          <button
            className="px-2.5 py-1 rounded border border-slate-700 text-slate-400 hover:bg-slate-800 shrink-0"
            onClick={discardDraft}
          >
            Discard
          </button>
        </div>
      )}

      {/* Search bar */}
      <SearchBar
        search={search}
        onNext={handleSearchNext}
        onPrev={handleSearchPrev}
        onReplace={handleSearchReplace}
        onReplaceAll={handleSearchReplaceAll}
        onClose={handleSearchClose}
      />

      {/* Split-pane editor */}
      <div className="flex-1 overflow-hidden flex flex-col md:flex-row relative">
        {/* Chinese source panel (left) */}
        {hasSource && showSource && (
          <div
            ref={chineseRef}
            onScroll={handleChineseScroll}
            onMouseUp={handleChineseMouseUp}
            onDoubleClick={handleChineseDblClick}
            className="w-full md:w-1/2 h-1/2 md:h-auto overflow-y-auto bg-slate-950 border-b md:border-b-0 md:border-r border-slate-800 select-text"
          >
            <div className="p-4">
              <div className="text-xs text-slate-600 uppercase tracking-wider mb-3 font-medium">
                Source ({untranslatedLines.length} lines)
              </div>

              {/* New entities discovered in this chapter */}
              {newInChapter.length > 0 && (
                <details className="mb-3 text-xs">
                  <summary className="cursor-pointer text-indigo-400/70 hover:text-indigo-400 select-none">
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
                          onClick={() => entityModalUrl.open(ent.id)}
                        >
                          <span className="text-slate-400">{ent.untranslated}</span>
                          <span className="text-slate-600">→</span>
                          <span className="text-slate-300">{ent.translation}</span>
                        </span>
                      )
                    })}
                  </div>
                </details>
              )}

              {untranslatedLines.map((line, i) => (
                <div
                  key={i}
                  data-line-idx={i}
                  ref={el => lineRefs.current[i] = el}
                  className={`group flex font-mono text-sm leading-relaxed transition-colors duration-100 ${
                    i === activeLine
                      ? 'bg-indigo-500/15 border-l-2 border-indigo-400 -ml-px'
                      : 'border-l-2 border-transparent -ml-px'
                  }`}
                >
                  <span className={`w-10 shrink-0 text-right pr-3 select-none text-xs leading-relaxed ${
                    i === activeLine ? 'text-indigo-400' : 'text-slate-700'
                  }`}>
                    {i + 1}
                  </span>
                  <span className={`flex-1 pr-4 py-px break-all ${
                    i === activeLine ? 'text-slate-100' : 'text-slate-400'
                  }`}>
                    <HighlightedChineseLine
                      line={line}
                      matcher={chineseMatcher}
                      annotation={annotations[i]}
                      onEntityClick={handleEntityClick}
                      searchMatches={chineseSearchMatches[i]}
                      activeMatch={currentChapterActiveMatch?.field === 'untranslated' && currentChapterActiveMatch?.line === i ? currentChapterActiveMatch : null}
                    />
                  </span>
                  <button
                    className="shrink-0 w-6 opacity-0 group-hover:opacity-60 hover:!opacity-100
                               text-slate-500 hover:text-emerald-400 transition-opacity select-none"
                    title="Retranslate this line"
                    onClick={(e) => { e.stopPropagation(); handleRetranslateLine(i) }}
                  >
                    <Languages size={12} />
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* English translation panel (right) — with overlay for entity highlights */}
        <div className={`${hasSource && showSource ? 'w-full md:w-1/2 h-1/2 md:h-auto' : 'flex-1'} flex flex-col overflow-hidden`}>
          {hasSource && showSource && (
            <div className="px-4 pt-4 pb-1">
              <div className="text-xs text-slate-600 uppercase tracking-wider font-medium">
                Translation (editable)
              </div>
              {newInChapter.length > 0 && (
                <div className="text-xs mb-3">&nbsp;</div>
              )}
            </div>
          )}
          {showPreview ? (
            <div className="flex-1 overflow-y-auto bg-slate-950">
              <div className="chapter-markdown max-w-2xl mx-auto px-6 py-6 text-slate-200 text-[15px] leading-relaxed">
                {splitSegments(textLines).map((seg, i) => seg.type === 'img' ? (
                  <img key={i} src={previewIllustrationSrc(seg.id)}
                    alt="" loading="lazy" className="block mx-auto my-6 max-w-full rounded" />
                ) : (
                  <div key={i} dangerouslySetInnerHTML={{ __html: renderSegment(seg) }} />
                ))}
              </div>
            </div>
          ) : (
          <div className="flex-1 relative overflow-hidden bg-slate-950 flex">
            {/* Hidden mirror for measuring wrapped line heights */}
            <div
              ref={mirrorRef}
              aria-hidden="true"
              className="font-mono text-sm leading-relaxed whitespace-pre-wrap"
              style={{
                position: 'absolute', visibility: 'hidden', height: 0, overflow: 'hidden',
                overflowWrap: 'break-word', wordBreak: 'break-word',
              }}
            />
            {/* Line number gutter */}
            <div
              className="shrink-0 select-none overflow-hidden text-right font-mono text-xs text-slate-700"
              style={{
                width: '2.5rem',
                paddingTop: hasSource && showSource ? '0.75rem' : '1.25rem',
                paddingRight: '0.5rem',
              }}
            >
              <div ref={gutterInnerRef} style={{ transform: `translateY(-${overlayScrollRef.current}px)` }}>
                {lineHeights.map((h, i) => (
                  <div
                    key={i}
                    className={i === activeLine ? 'text-indigo-400' : ''}
                    style={{ height: h + 'px', lineHeight: 'normal', paddingTop: '1px' }}
                  >
                    {i + 1}
                  </div>
                ))}
              </div>
            </div>
            {/* Text area + overlay container */}
            <div className="flex-1 relative overflow-hidden">
              {/* Backdrop: renders entity highlights behind the textarea */}
              {((showEntities && englishMatcher.list.length > 0) || Object.keys(translatedSearchMatches).length > 0) && (
                <div ref={backdropWrapRef} className="absolute inset-0 pointer-events-none">
                  <EnglishBackdrop
                    text={debouncedText}
                    textLines={debouncedTextLines}
                    matcher={showEntities ? englishMatcher : emptyMatcher}
                    scrollTop={overlayScrollRef.current}
                    paddingClass={hasSource && showSource ? 'pr-4 pt-3 pb-4' : 'pr-5 pt-5 pb-5'}
                    searchMatches={translatedSearchMatches}
                    activeMatch={currentChapterActiveMatch?.field === 'translated' ? currentChapterActiveMatch : null}
                  />
                </div>
              )}
              <textarea
                ref={textareaRef}
                className={`absolute inset-0 w-full h-full text-slate-100 font-mono text-sm leading-relaxed
                           resize-none outline-none border-0
                           selection:bg-indigo-600/40 ${hasSource && showSource ? 'pr-4 pt-3 pb-4' : 'pr-5 pt-5 pb-5'}`}
                style={{
                  // Always transparent so the dark bg-slate-950 parent (and the
                  // highlight backdrop, when present) shows through. Without this,
                  // chapters with no entities — e.g. original works — render the
                  // textarea's default white background.
                  background: 'transparent',
                  caretColor: '#e2e8f0',
                }}
                value={text}
                onChange={handleChange}
                onKeyDown={handleKeyDown}
                onKeyUp={updateActiveLine}
                onClick={updateActiveLine}
                onScroll={handleTextareaScroll}
                spellCheck={false}
                placeholder="No translation content yet."
              />
            </div>
          </div>
          )}
        </div>

        {/* Selection toolbar (floating) */}
        <SelectionToolbar
          position={selToolbar}
          onLookup={handleToolbarLookup}
          onRetranslate={handleToolbarRetranslate}
        />

        {/* Dictionary popup (floating) */}
        {dictQuery && (
          <DictModal
            query={dictQuery}
            data={dictData}
            loading={dictLoading}
            error={dictError}
            position={dictPos}
            onClose={closeDictModal}
          />
        )}
      </div>

      {/* Retranslation modal */}
      {retranslateModal && (
        <RetranslateModal
          chineseText={retranslateModal.text}
          lineIndex={retranslateModal.lineIndex}
          allLines={untranslatedLines}
          bookId={bookId}
          providers={providers}
          onResult={handleRetranslateResult}
          onClose={closeRetranslateModal}
        />
      )}

      {editingEntity && (
        <EntityFormModal
          entity={editingEntity}
          onClose={entityModalUrl.close}
          onSaved={handleEntitySaved}
        />
      )}

      {pronounRepairOpen && (
        <PronounRepairModal
          bookId={parseInt(bookId)}
          chapterNumber={parseInt(chapterNum)}
          onResult={handlePronounRepairResult}
          onClose={() => setPronounRepairOpen(false)}
        />
      )}

      {pronounRepairToast && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50
                        bg-slate-800 border border-emerald-700/50 rounded-lg shadow-xl
                        px-4 py-3 flex items-center gap-3 text-sm text-slate-200">
          <Sparkles size={14} className="text-emerald-400" />
          <span>
            {pronounRepairToast.paragraphs_changed > 0
              ? `Repaired ${pronounRepairToast.paragraphs_changed} paragraph${pronounRepairToast.paragraphs_changed === 1 ? '' : 's'} for ${pronounRepairToast.character_name} (${pronounRepairToast.windows_examined} windows examined)`
              : `No changes needed for ${pronounRepairToast.character_name} (${pronounRepairToast.windows_examined} windows examined)`}
            {pronounRepairToast.errors ? ` — ${pronounRepairToast.errors} window error(s)` : ''}
          </span>
          <button
            className="text-slate-500 hover:text-slate-300 transition-colors"
            onClick={() => clearPronounToast()}
          >
            <X size={14} />
          </button>
        </div>
      )}

      {/* Replace-all undo toast */}
      {undoInfo && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50
                        bg-slate-800 border border-slate-600 rounded-lg shadow-xl
                        px-4 py-3 flex items-center gap-3 text-sm text-slate-200">
          <span>
            Replaced {undoInfo.count} match{undoInfo.count !== 1 ? 'es' : ''}
            {undoInfo.type === 'book' ? ' across book' : ''}
          </span>
          <button
            className="px-3 py-1 rounded bg-indigo-600 hover:bg-indigo-500
                       text-white font-medium transition-colors"
            onClick={handleUndo}
          >
            Undo
          </button>
          <button
            className="text-slate-500 hover:text-slate-300 transition-colors"
            onClick={() => clearUndoInfo()}
          >
            <X size={14} />
          </button>
        </div>
      )}
    </div>
  )
}
