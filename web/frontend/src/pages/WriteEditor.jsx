import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import { useEditor, EditorContent } from '@tiptap/react'
import {
  ArrowLeft, ChevronLeft, ChevronRight, Loader2, Plus, BookOpen, X, Languages,
} from 'lucide-react'
import { api } from '../services/api'
import { useSite } from '../App'
import { buildWriteExtensions } from '../lib/writeExtensions'
import { linesToDoc, docToLines, roundTrip } from '../lib/writeMarkdown'
import { cleanPastedHTML, cleanPastedText } from '../lib/pasteCleanup'
import { docToBBCode } from '../lib/bbcode'
import { copyToClipboard } from '../utils/clipboard'
import { splitSegments, renderSegment } from '../lib/chapterMarkdown'
import { trimEmptyLines } from '../lib/editorHighlights'
import { useUnsavedGuard } from '../hooks/useUnsavedGuard'
import { useTypewriterScroll } from '../hooks/useTypewriterScroll'
import { useGrammarCheck } from '../hooks/useGrammarCheck'
import { useTransientFlag } from '../hooks/useTransientFlag'
import { useLocalStorage } from '../hooks/useLocalStorage'
import WriteToolbar from '../components/write/WriteToolbar'
import SelectionBubbleMenu from '../components/write/SelectionBubbleMenu'
import FocusToolbar from '../components/write/FocusToolbar'
import GrammarPopover from '../components/write/GrammarPopover'
import SuggestionsPane from '../components/write/SuggestionsPane'
import StatusBar from '../components/write/StatusBar'
import RevisionsPanel from '../components/write/RevisionsPanel'
import PublishMenu from '../components/PublishMenu'
import { IllustrationUrlContext } from '../components/write/IllustrationNode'

const IDLE_AUTOSAVE_MS = 30_000  // autosave after 30s without typing
const MAX_AUTOSAVE_MS = 90_000   // …or at most 90s after edits began
const DRAFT_DEBOUNCE_MS = 2_000

function countWords(editor) {
  const doc = editor.state.doc
  const text = doc.textBetween(0, doc.content.size, '\n', ' ')
  const m = text.match(/\S+/g)
  return m ? m.length : 0
}

const todayKey = () => new Date().toISOString().slice(0, 10)

/**
 * WYSIWYG writing editor for original works (books.is_original). Content is
 * stored in the same Markdown line-array format the translation pipeline
 * uses, converted via lib/writeMarkdown — the Reader, EPUB export, and
 * WordPress publishing are untouched. Every save is round-trip-verified;
 * a mismatch blocks the save instead of corrupting the chapter.
 */
export default function WriteEditor() {
  const { bookId, chapterNum } = useParams()
  const navigate = useNavigate()
  const { site_name } = useSite()
  const draftKey = `chapterDraft:${bookId}:${chapterNum}`

  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [book, setBook] = useState(null)
  const [chapter, setChapter] = useState(null)
  const [chapterList, setChapterList] = useState([])
  const [title, setTitle] = useState('')
  const [unsupported, setUnsupported] = useState([])
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState(null)
  const [conflict, setConflict] = useState(null)
  const [draftOffer, setDraftOffer] = useState(null)
  const [publishedAt, setPublishedAt] = useState(null)
  const [words, setWords] = useState(0)
  const [showPreview, setShowPreview] = useState(false)
  const [revisionsOpen, setRevisionsOpen] = useState(false)
  const [suggestionsOpen, setSuggestionsOpen] = useState(false)
  const [focusMode, setFocusMode] = useState(false)
  const [tick, setTick] = useState(0) // re-render for toolbar isActive states + preview freshness
  const [savedFlash, flashSaved] = useTransientFlag(2500)
  const [autosavedFlash, flashAutosaved] = useTransientFlag(2500)
  const [bbcodeCopied, flashBBCodeCopied] = useTransientFlag(1500)

  // Daily goal
  const [dailyGoal, setDailyGoal] = useLocalStorage('write.dailyGoal', null)
  const [dailyWords, setDailyWords] = useLocalStorage('write.dailyWords', { date: '', words: 0 })
  const todayWords = dailyWords.date === todayKey() ? dailyWords.words : 0

  // Refs for values read inside timers/editor callbacks (avoid stale closures)
  const loadingRef = useRef(true)
  const dirtyRef = useRef(false)
  const savingRef = useRef(false)
  const conflictRef = useRef(null)
  const titleRef = useRef('')
  const lockRef = useRef(null)         // translation_date optimistic-lock token
  const baselineRef = useRef(0)        // words at load (session counter)
  const lastCountedRef = useRef(0)     // words at last save (daily counter)
  const idleTimerRef = useRef(null)
  const maxTimerRef = useRef(null)
  const draftTimerRef = useRef(null)
  const doSaveRef = useRef(() => {})
  const scrollRef = useRef(null)

  const markDirty = useCallback((v) => { dirtyRef.current = v; setDirty(v) }, [])
  titleRef.current = title
  conflictRef.current = conflict

  const clearAutosaveTimers = useCallback(() => {
    clearTimeout(idleTimerRef.current)
    clearTimeout(maxTimerRef.current)
    idleTimerRef.current = null
    maxTimerRef.current = null
  }, [])

  const scheduleAutosave = useCallback(() => {
    clearTimeout(idleTimerRef.current)
    idleTimerRef.current = setTimeout(() => doSaveRef.current({ autosave: true }), IDLE_AUTOSAVE_MS)
    if (!maxTimerRef.current) {
      maxTimerRef.current = setTimeout(() => {
        maxTimerRef.current = null
        doSaveRef.current({ autosave: true })
      }, MAX_AUTOSAVE_MS)
    }
  }, [])

  const editorRef = useRef(null)
  const scheduleDraft = useCallback(() => {
    clearTimeout(draftTimerRef.current)
    draftTimerRef.current = setTimeout(() => {
      const ed = editorRef.current
      if (!ed || !dirtyRef.current) return
      try {
        localStorage.setItem(draftKey, JSON.stringify({
          text: docToLines(ed.getJSON()).join('\n'),
          title: titleRef.current,
          savedAt: Date.now(),
        }))
      } catch { /* quota exceeded — ignore */ }
    }, DRAFT_DEBOUNCE_MS)
  }, [draftKey])

  const extensions = useMemo(() => buildWriteExtensions(), [])
  const editor = useEditor({
    extensions,
    content: { type: 'doc', content: [{ type: 'paragraph' }] },
    editorProps: {
      attributes: {
        class: 'chapter-markdown outline-none min-h-[60vh] px-1 py-4 text-slate-200 text-[1.05rem] leading-relaxed caret-indigo-400',
      },
      transformPastedHTML: cleanPastedHTML,
      transformPastedText: cleanPastedText,
    },
    onUpdate: ({ editor: ed }) => {
      if (loadingRef.current) return
      markDirty(true)
      setSaveError(null)
      setWords(countWords(ed))
      scheduleAutosave()
      scheduleDraft()
      setTick((t) => t + 1)
    },
    onSelectionUpdate: () => setTick((t) => t + 1),
  })
  editorRef.current = editor

  useEffect(() => {
    document.title = `Write: ${title || `Chapter ${chapterNum}`}`
    return () => { document.title = site_name }
  }, [title, chapterNum, site_name])

  useUnsavedGuard(dirty)
  useTypewriterScroll(editor, focusMode, scrollRef)
  // Focus mode never renders the pane; close it so the popover (which the
  // open pane suppresses) keeps working there.
  useEffect(() => {
    if (focusMode) setSuggestionsOpen(false)
  }, [focusMode])
  // `!loading` gates polish-job re-attach until the chapter is in the editor.
  const grammar = useGrammarCheck(editor, bookId, chapterNum, !loading)

  // The hook gates its scroll-close-popover behavior while the pane is open
  // (scrolling would otherwise constantly clear the pane's selected row).
  useEffect(() => {
    grammar.setPaneOpen(suggestionsOpen)
  }, [suggestionsOpen, grammar.setPaneOpen]) // eslint-disable-line react-hooks/exhaustive-deps

  // Native (OS/browser) spellcheck on the contenteditable can't see the book
  // dictionary (entities), so it permanently squiggles OC/fandom terms and
  // double-flags alongside LanguageTool. Hand the surface to our checker when
  // it's enabled; fall back to the native one when it's not.
  useEffect(() => {
    if (!editor || editor.isDestroyed) return
    const dom = editor.view.dom
    dom.setAttribute('spellcheck', grammar.enabled ? 'false' : 'true')
    dom.setAttribute('autocorrect', grammar.enabled ? 'off' : 'on') // Safari/macOS
  }, [editor, grammar.enabled])

  // ------------------------------------------------------------------
  // Load
  // ------------------------------------------------------------------
  useEffect(() => {
    if (!editor) return undefined
    let cancelled = false
    loadingRef.current = true
    setLoading(true)
    setError(null)
    setUnsupported([])
    setConflict(null)
    setDraftOffer(null)
    setShowPreview(false)
    markDirty(false)
    clearAutosaveTimers()

    Promise.all([
      api.getChapter(parseInt(bookId), parseInt(chapterNum)),
      api.getBook(parseInt(bookId)),
      api.listChapters(parseInt(bookId)),
    ])
      .then(([ch, bk, chaps]) => {
        if (cancelled) return
        // Translation books may use this editor too (opt-in via the Books
        // page or URL) — their default entry stays the split-pane /edit.
        setBook(bk)
        setChapter(ch)
        setTitle(ch.title || '')
        setPublishedAt(ch.published_at || null)
        setChapterList((chaps.chapters || []).map((c) => c.chapter).sort((a, b) => a - b))
        lockRef.current = ch.translation_date || null

        const lines = trimEmptyLines(Array.isArray(ch.content) ? ch.content : [])
        const { doc, unsupported: unsup } = linesToDoc(lines)
        setUnsupported(unsup)
        if (!unsup.length) {
          editor.commands.setContent(doc, { emitUpdate: false })
          editor.commands.setTextSelection(0)
          const w = countWords(editor)
          setWords(w)
          baselineRef.current = w
          lastCountedRef.current = w
        }

        // Offer to restore an unsaved local draft (same semantics as
        // ChapterEditor): skip when stale or identical to what's saved.
        try {
          const raw = localStorage.getItem(draftKey)
          if (raw) {
            const d = JSON.parse(raw)
            const translatedAt = ch.translation_date ? Date.parse(ch.translation_date) : NaN
            const stale = Number.isFinite(translatedAt) && d.savedAt <= translatedAt
            const differs = d.text !== lines.join('\n') || (d.title != null && d.title !== ch.title)
            if (!stale && differs && typeof d.text === 'string') setDraftOffer(d)
            else localStorage.removeItem(draftKey)
          }
        } catch { /* corrupt draft — ignore */ }
      })
      .catch((e) => !cancelled && setError(e.message))
      .finally(() => {
        if (!cancelled) {
          setLoading(false)
          loadingRef.current = false
        }
      })
    return () => { cancelled = true }
  }, [bookId, chapterNum, editor]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => () => {
    clearAutosaveTimers()
    clearTimeout(draftTimerRef.current)
  }, [clearAutosaveTimers])

  // ------------------------------------------------------------------
  // Save
  // ------------------------------------------------------------------
  const addDailyWords = useCallback((delta) => {
    setDailyWords((prev) => {
      const t = todayKey()
      return prev.date === t
        ? { date: t, words: prev.words + delta }
        : { date: t, words: delta }
    })
  }, [setDailyWords])

  const doSave = useCallback(async ({ snapshot = false, autosave = false, force = false } = {}) => {
    const ed = editorRef.current
    if (!ed || savingRef.current || loadingRef.current) return false
    if (autosave && (!dirtyRef.current || conflictRef.current)) return false
    if (conflictRef.current && !force) return false

    const rt = roundTrip(ed.getJSON())
    if (!rt.ok) {
      const HINTS = {
        'table:structure': 'a table is missing its header row — click into it and use the “Hdr” toggle in the table controls',
        'table:merged-cells': 'a table contains merged cells, which markdown tables can’t express — split them first',
        'table-cell:heading': 'a table cell contains a heading, which has no table form — convert it to bold text',
        'table-cell:codeBlock': 'a table cell contains a code block, which has no table form — move it outside the table',
        'table-cell:horizontalRule': 'a table cell contains a horizontal rule, which has no table form — remove it',
        'table-cell:illustration': 'a table cell contains an illustration, which has no table form — move it outside the table',
        'table-cell:table': 'a table is nested inside a table cell — flatten it',
        'table-cell:marker-literal': 'a table cell contains literal ⟦…⟧ marker text, which would corrupt the stored table — remove or reword it',
        'sentinel:literal': 'the text contains literal ⟦…⟧ marker characters (table, underline, or color markers), which would be misread as formatting — remove or reword them',
      }
      const detail = [...rt.warnings, ...rt.unsupported].join(', ')
      const hints = [...new Set(rt.warnings.map((w) => HINTS[w]).filter(Boolean))]
      const where = rt.mismatch
        ? ` The problem is in block ${rt.mismatch.index + 1}${rt.mismatch.excerpt ? ` (“${rt.mismatch.excerpt}…”)` : ''} — usually bold/italic wrapped around unusual punctuation; retyping that formatting fixes it.`
        : ''
      setSaveError(`Save blocked: content wouldn't survive the markdown round-trip${detail ? ` (${detail})` : ''}.${hints.length ? ` Fix: ${hints.join('; ')}.` : ''}${where} Your text is safe in the editor and the local draft.`)
      return false
    }

    const docAtSave = ed.state.doc
    const titleAtSave = titleRef.current
    savingRef.current = true
    setSaving(true)
    setSaveError(null)
    try {
      const res = await api.updateChapter(parseInt(bookId), parseInt(chapterNum), {
        content: rt.lines,
        title: titleAtSave,
        snapshot,
        autosave,
        ...(force ? {} : { expected_translation_date: lockRef.current }),
      })
      lockRef.current = res.translation_date || lockRef.current
      setConflict(null)
      // Only mark clean if nothing changed while the request was in flight.
      if (ed.state.doc.eq(docAtSave) && titleRef.current === titleAtSave) {
        markDirty(false)
        clearAutosaveTimers()
        clearTimeout(draftTimerRef.current)
        localStorage.removeItem(draftKey)
      }
      const w = countWords(ed)
      const delta = w - lastCountedRef.current
      if (delta > 0) addDailyWords(delta)
      lastCountedRef.current = w
      if (snapshot) flashSaved()
      else flashAutosaved()
      return true
    } catch (e) {
      if (e.status === 409) {
        setConflict({ serverDate: e.detail?.translation_date || null })
        clearAutosaveTimers()
      } else {
        setSaveError(e.message)
      }
      return false
    } finally {
      savingRef.current = false
      setSaving(false)
    }
  }, [bookId, chapterNum, draftKey, addDailyWords, markDirty, clearAutosaveTimers, flashSaved, flashAutosaved])
  useEffect(() => { doSaveRef.current = doSave }, [doSave])

  const handleManualSave = useCallback(() => doSave({ snapshot: true }), [doSave])

  // Conflict resolution
  const reloadFromServer = useCallback(async () => {
    try {
      const ch = await api.getChapter(parseInt(bookId), parseInt(chapterNum))
      const lines = trimEmptyLines(Array.isArray(ch.content) ? ch.content : [])
      const { doc, unsupported: unsup } = linesToDoc(lines)
      setUnsupported(unsup)
      if (!unsup.length && editorRef.current) {
        editorRef.current.commands.setContent(doc, { emitUpdate: false })
        setWords(countWords(editorRef.current))
      }
      setChapter(ch)
      setTitle(ch.title || '')
      lockRef.current = ch.translation_date || null
      setConflict(null)
      markDirty(false)
      grammar.clearAll()
      localStorage.removeItem(draftKey)
    } catch (e) {
      setSaveError(e.message)
    }
  }, [bookId, chapterNum, draftKey, markDirty, grammar.clearAll]) // eslint-disable-line react-hooks/exhaustive-deps

  // ------------------------------------------------------------------
  // Keyboard shortcuts
  // ------------------------------------------------------------------
  useEffect(() => {
    const handler = (e) => {
      if (e.defaultPrevented) return
      const mod = e.ctrlKey || e.metaKey
      // Ctrl+Alt combos: Ctrl+E is TipTap's inline-code toggle, and plain
      // Ctrl+Shift+P/H hit browser defaults — Alt keeps these collision-free.
      if (mod && !e.altKey && e.key === 's') {
        e.preventDefault()
        doSaveRef.current({ snapshot: true })
      } else if (mod && !e.altKey && e.shiftKey && (e.key === 'f' || e.key === 'F')) {
        e.preventDefault()
        setFocusMode((f) => !f)
      } else if (mod && e.altKey && (e.key === 'p' || e.key === 'P')) {
        e.preventDefault()
        if (!focusMode) setShowPreview((v) => !v)
      } else if (mod && e.altKey && (e.key === 'h' || e.key === 'H')) {
        e.preventDefault()
        setRevisionsOpen((v) => !v)
      } else if (mod && e.altKey && (e.key === 'g' || e.key === 'G')) {
        e.preventDefault()
        if (!focusMode) {
          setSuggestionsOpen((v) => !v)
          // Pane rows scroll/select in the editor — a hidden editor (preview)
          // would make that a no-op, so opening the pane exits preview.
          setShowPreview(false)
        }
      } else if (e.key === 'Escape') {
        // An open popover/panel (link editor, revisions, publish menu) owns
        // Esc — only exit focus mode when nothing else is dismissible.
        if (document.querySelector('[data-esc-guard]')) return
        setFocusMode(false)
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [focusMode])

  // ------------------------------------------------------------------
  // Draft restore / revisions / preview / navigation helpers
  // ------------------------------------------------------------------
  const restoreDraft = useCallback(() => {
    if (!draftOffer || !editorRef.current) return
    const { doc, unsupported: unsup } = linesToDoc(draftOffer.text.split('\n'))
    if (unsup.length) {
      // Don't silently no-op — tell the user why nothing happened. The offer
      // banner stays up so Discard remains available; the draft stays in
      // localStorage, so nothing is lost.
      setSaveError(
        `The draft couldn't be restored: it contains markdown constructs the write editor `
        + `doesn't support (${[...new Set(unsup)].slice(0, 4).join(', ')}). `
        + `Use Discard to drop the draft, or the translation editor to recover it.`
      )
      return
    }
    editorRef.current.commands.setContent(doc, { emitUpdate: false })
    setWords(countWords(editorRef.current))
    setTick((t) => t + 1) // emitUpdate:false skips onUpdate — refresh preview/toolbar
    grammar.clearAll() // decorations are meaningless after a full content swap
    if (draftOffer.title != null) setTitle(draftOffer.title)
    markDirty(true)
    scheduleAutosave()
    setDraftOffer(null)
  }, [draftOffer, markDirty, scheduleAutosave, grammar.clearAll]) // eslint-disable-line react-hooks/exhaustive-deps

  const discardDraft = useCallback(() => {
    localStorage.removeItem(draftKey)
    setDraftOffer(null)
  }, [draftKey])

  const handleRestoredRevision = useCallback((revision, translationDate) => {
    const ed = editorRef.current
    if (!ed) return
    const { doc, unsupported: unsup } = linesToDoc(trimEmptyLines(revision.content || []))
    if (!unsup.length) {
      ed.commands.setContent(doc, { emitUpdate: false })
      setWords(countWords(ed))
      setTick((t) => t + 1) // emitUpdate:false skips onUpdate — refresh preview/toolbar
      grammar.clearAll()
    }
    if (revision.title) setTitle(revision.title)
    lockRef.current = translationDate || lockRef.current
    setConflict(null)
    markDirty(false)
    localStorage.removeItem(draftKey)
  }, [draftKey, markDirty, grammar.clearAll]) // eslint-disable-line react-hooks/exhaustive-deps

  // `tick` bumps on every editor update, so reopening (or keeping) the
  // preview always reflects the latest content — `dirty` only flips once.
  const previewSegments = useMemo(() => {
    if (!showPreview || !editor) return []
    return splitSegments(docToLines(editor.getJSON()))
  }, [showPreview, editor, tick]) // eslint-disable-line react-hooks/exhaustive-deps

  const chIdx = chapterList.indexOf(parseInt(chapterNum))
  const prevCh = chIdx > 0 ? chapterList[chIdx - 1] : null
  const nextCh = chIdx >= 0 && chIdx < chapterList.length - 1 ? chapterList[chIdx + 1] : null

  const handleNewChapter = useCallback(async () => {
    try {
      const res = await api.createChapter(parseInt(bookId))
      navigate(`/books/${bookId}/chapters/${res.chapter_number}/write`)
    } catch (e) {
      setSaveError(e.message)
    }
  }, [bookId, navigate])

  const illustrationCtx = useMemo(
    () => ({ urls: chapter?.illustrations || {}, bookId }),
    [chapter, bookId])

  // Lives here (not in the toolbar): needs illustrationCtx, and the toolbar
  // sits outside the IllustrationUrlContext provider.
  const handleCopyBBCode = useCallback(() => {
    const ed = editorRef.current
    if (!ed) return
    copyToClipboard(docToBBCode(ed.getJSON(), { illustrationUrls: illustrationCtx.urls }))
      .then(() => flashBBCodeCopied())
      .catch((e) => setSaveError(`Copy failed: ${e.message}`))
  }, [illustrationCtx, flashBBCodeCopied])

  // ------------------------------------------------------------------
  // Render
  // ------------------------------------------------------------------
  if (loading) {
    return <div className="flex justify-center py-24"><Loader2 size={28} className="animate-spin text-indigo-400" /></div>
  }
  if (error) {
    return (
      <div className="max-w-2xl mx-auto mt-12 card p-6">
        <p className="text-rose-400">{error}</p>
        <Link className="btn-secondary inline-block mt-4" to={`/books/${bookId}`}>Back to book</Link>
      </div>
    )
  }
  if (unsupported.length) {
    return (
      <div className="max-w-2xl mx-auto mt-12 card p-6 space-y-3">
        <h2 className="font-semibold text-slate-200">Can’t open in the write editor</h2>
        <p className="text-sm text-slate-400">
          This chapter contains markdown constructs the write editor doesn’t support
          ({unsupported.slice(0, 4).join(', ')}). Saving here would drop them.
        </p>
        <Link className="btn-primary inline-block" to={`/books/${bookId}/chapters/${chapterNum}/edit`}>
          Open in the translation editor instead
        </Link>
      </div>
    )
  }

  const statusBar = (
    <StatusBar
      words={words}
      sessionWords={words - baselineRef.current}
      dailyWords={todayWords}
      dailyGoal={dailyGoal}
      onSetGoal={setDailyGoal}
      dirty={dirty}
      saving={saving}
      savedFlash={savedFlash}
      autosavedFlash={autosavedFlash}
      conflict={!!conflict}
    />
  )

  // Save errors and conflicts float above the viewport bottom so they're
  // visible wherever you're scrolled (and in focus mode, which renders no
  // banners at all). z-50 sits above the sticky toolbar/popovers.
  const alerts = (saveError || conflict) && (
    <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 w-[calc(100%-2rem)] max-w-2xl space-y-2">
      {conflict && (
        <div className="px-4 py-2.5 rounded-lg border border-rose-600/60 bg-slate-900/95 backdrop-blur shadow-2xl text-sm space-y-2">
          <p className="text-rose-200">
            This chapter changed on the server since you loaded it (e.g. a restore in another tab).
            Autosave is paused.
          </p>
          <div className="flex gap-2">
            <button className="btn-secondary text-xs px-2.5 py-1" onClick={reloadFromServer}>
              Load server version (discards my edits)
            </button>
            <button className="btn-primary text-xs px-2.5 py-1" onClick={() => doSave({ snapshot: true, force: true })}>
              Overwrite with my version
            </button>
          </div>
        </div>
      )}
      {saveError && (
        <div className="px-4 py-2.5 rounded-lg border border-rose-600/60 bg-slate-900/95 backdrop-blur shadow-2xl flex items-start gap-2 text-sm">
          <p className="text-rose-200 flex-1">{saveError}</p>
          <button className="btn-ghost p-1" onClick={() => setSaveError(null)}><X size={13} /></button>
        </div>
      )}
    </div>
  )

  const editorSurface = (
    <IllustrationUrlContext.Provider value={illustrationCtx}>
      {/* gram-pane-open strengthens the active-issue highlight: with the
          pane docked the popover is suppressed, so the highlight is the
          only in-editor anchor for the selected suggestion. */}
      <div className={suggestionsOpen ? 'gram-pane-open' : undefined}>
        <EditorContent editor={editor} />
      </div>
      <SelectionBubbleMenu editor={editor} />
      {grammar.active && !suggestionsOpen && (
        <GrammarPopover
          active={grammar.active}
          onApply={grammar.applySuggestion}
          onDismiss={grammar.dismiss}
          onClose={grammar.closePopover}
          onAddToDictionary={grammar.addToDictionary}
          onIgnoreRule={grammar.ignoreRule}
        />
      )}
    </IllustrationUrlContext.Provider>
  )

  if (focusMode) {
    return (
      <div className="fixed inset-0 z-50 bg-slate-950 flex flex-col">
        <div ref={scrollRef} className="flex-1 overflow-y-auto">
          <div className="max-w-[70ch] mx-auto px-6 pt-[35vh] pb-[45vh]">
            {editorSurface}
          </div>
        </div>
        <FocusToolbar
          editor={editor}
          dirty={dirty}
          saving={saving}
          savedFlash={savedFlash}
          onExit={() => setFocusMode(false)}
        />
        <div className="opacity-50 hover:opacity-100 transition-opacity">{statusBar}</div>
        {alerts}
      </div>
    )
  }

  return (
    // Opening the suggestions pane widens the column and docks the pane on
    // the right — the editor shifts left instead of being overlaid.
    <div className={`${suggestionsOpen ? 'max-w-7xl' : 'max-w-4xl'} mx-auto pb-4`}>
      {/* Header: book nav + chapter prev/next */}
      <div className="flex items-center gap-2 py-3 text-sm">
        <Link to={`/books/${bookId}`} className="btn-ghost p-1.5" title="Back to book">
          <ArrowLeft size={15} />
        </Link>
        <span className="text-slate-400 truncate">{book?.title}</span>
        <span className="text-slate-600">·</span>
        <span className="text-slate-500">Chapter {chapterNum}</span>
        <div className="flex-1" />
        <PublishMenu
          bookId={parseInt(bookId)}
          chapterNum={parseInt(chapterNum)}
          publishedAt={publishedAt}
          onChanged={setPublishedAt}
          beforePublish={() => (dirtyRef.current ? doSave({ snapshot: true }) : Promise.resolve(true))}
        />
        <Link
          to={`/read/${bookId}?chapter=${chapterNum}`}
          className="btn-ghost p-1.5" title="Read in the public reader"
        ><BookOpen size={14} /></Link>
        {book && (
          <Link
            to={`/books/${bookId}/chapters/${chapterNum}/edit`}
            className="btn-ghost p-1.5"
            title={book.is_original ? 'Open in split-pane editor' : 'Open in translation editor (split-pane)'}
          ><Languages size={14} /></Link>
        )}
        {prevCh !== null && (
          <Link to={`/books/${bookId}/chapters/${prevCh}/write`} className="btn-ghost p-1.5" title={`Chapter ${prevCh}`}>
            <ChevronLeft size={15} />
          </Link>
        )}
        {nextCh !== null ? (
          <Link to={`/books/${bookId}/chapters/${nextCh}/write`} className="btn-ghost p-1.5" title={`Chapter ${nextCh}`}>
            <ChevronRight size={15} />
          </Link>
        ) : book?.is_original ? (
          <button className="btn-ghost p-1.5" title="New chapter" onClick={handleNewChapter}>
            <Plus size={15} />
          </button>
        ) : null}
      </div>

      {/* Draft restore banner */}
      {draftOffer && (
        <div className="mb-3 px-4 py-2.5 rounded border border-amber-600/40 bg-amber-500/10 flex items-center gap-3 text-sm">
          <span className="text-amber-200 flex-1">
            An unsaved draft from {new Date(draftOffer.savedAt).toLocaleString()} was found.
          </span>
          <button className="btn-primary text-xs px-2.5 py-1" onClick={restoreDraft}>Restore</button>
          <button className="btn-secondary text-xs px-2.5 py-1" onClick={discardDraft}>Discard</button>
        </div>
      )}

      {/* Polish suggestions whose text changed before the response arrived */}
      {grammar.leftovers.length > 0 && (
        <div className="mb-3 px-4 py-2.5 rounded border border-purple-600/40 bg-purple-500/10 text-sm space-y-1.5">
          <div className="flex items-center">
            <p className="text-purple-200 flex-1 text-xs font-medium">
              {grammar.leftovers.length} polish suggestion{grammar.leftovers.length > 1 ? 's' : ''} couldn’t
              be matched to the current text:
            </p>
            <button className="btn-ghost p-1" onClick={grammar.clearLeftovers}><X size={13} /></button>
          </div>
          {grammar.leftovers.map((s, i) => (
            <p key={i} className="text-xs text-slate-300">
              <span className="line-through text-slate-500">{s.find}</span>
              {' → '}
              <span className="text-purple-200">{s.replace}</span>
              {s.reason && <span className="text-slate-500"> — {s.reason}</span>}
            </p>
          ))}
        </div>
      )}

      <div className="flex gap-4 items-start">
      <div className="card flex-1 min-w-0">
        {/* Sticky: opaque bg (content scrolls under it), top-12 clears the
            fixed mobile top bar (matches main's pt-12 md:pt-0). */}
        <div className="sticky top-12 md:top-0 z-20 px-3 py-2 border-b border-slate-700/60 bg-slate-800 rounded-t-lg">
          <WriteToolbar
            editor={editor}
            tick={tick}
            saving={saving}
            dirty={dirty}
            showPreview={showPreview}
            onSave={handleManualSave}
            onTogglePreview={() => setShowPreview((v) => !v)}
            onToggleRevisions={() => setRevisionsOpen((v) => !v)}
            onToggleFocus={() => setFocusMode(true)}
            onCopyBBCode={handleCopyBBCode}
            bbcodeCopied={bbcodeCopied}
            grammar={grammar}
            suggestionsOpen={suggestionsOpen}
            onToggleSuggestions={() => {
              setSuggestionsOpen((v) => !v)
              setShowPreview(false)
            }}
          />
        </div>

        {/* Chapter title */}
        <input
          className="w-full bg-transparent px-6 pt-5 pb-1 text-xl font-semibold text-slate-100 outline-none placeholder:text-slate-600"
          value={title}
          placeholder="Chapter title"
          onChange={(e) => {
            setTitle(e.target.value)
            markDirty(true)
            scheduleAutosave()
            scheduleDraft()
          }}
          onKeyDown={(e) => {
            // Enter commits the title and drops the cursor into the body.
            if (e.key === 'Enter') {
              e.preventDefault()
              editorRef.current?.commands.focus('start')
            }
          }}
        />

        <div className="px-6 pb-6">
          {showPreview ? (
            <div className="min-h-[60vh] py-4 text-slate-200 text-[1.05rem] leading-relaxed">
              {previewSegments.map((seg, i) =>
                seg.type === 'img' ? (
                  <div key={i} className="my-4 flex justify-center">
                    <img
                      src={illustrationCtx.urls[seg.id] || `/api/books/${bookId}/illustration/${seg.id}`}
                      alt="" className="max-h-[60vh] rounded"
                    />
                  </div>
                ) : (
                  <div key={i} className="chapter-markdown"
                    dangerouslySetInnerHTML={{ __html: renderSegment(seg) }} />
                ))}
            </div>
          ) : editorSurface}
        </div>

        {statusBar}
      </div>

      {suggestionsOpen && (
        <SuggestionsPane grammar={grammar} onClose={() => setSuggestionsOpen(false)} />
      )}
      </div>

      {revisionsOpen && (
        <RevisionsPanel
          bookId={parseInt(bookId)}
          chapterNum={parseInt(chapterNum)}
          currentWords={words}
          illustrationUrls={illustrationCtx.urls}
          onRestored={handleRestoredRevision}
          onClose={() => setRevisionsOpen(false)}
        />
      )}
      {alerts}
    </div>
  )
}
