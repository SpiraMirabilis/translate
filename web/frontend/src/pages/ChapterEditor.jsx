/**
 * ChapterEditor — split-pane translation editor for a single chapter.
 * Left: read-only Chinese source text with line highlighting, dictionary lookup,
 *       inline retranslation annotations, and entity highlighting.
 * Right: editable English translation with synchronized scrolling and
 *        entity highlighting via overlay technique.
 * Reached via /books/:bookId/chapters/:chapterNum/edit
 */
import { useState, useEffect, useRef, useCallback, useMemo, memo } from 'react'
import { useParams, useNavigate, useSearchParams, useBlocker, Link } from 'react-router-dom'
import { api } from '../services/api'
import { bustUrl } from '../services/cacheBust'
import { ArrowLeft, ChevronLeft, ChevronRight, Save, Loader2, Check, AlertCircle, X, BookOpen, Languages, CheckCircle2, Search, Pencil, Globe, Sparkles, Bold, Italic, Code, Link2, Heading, List, Quote, Minus, Eye } from 'lucide-react'
import { renderBlock, splitSegments } from '../lib/chapterMarkdown'
import { trimEmptyLines, pinyinToMarked, buildMatcher, highlightSegments, applySearchHighlights } from '../lib/editorHighlights'
import ComboBox from '../components/ComboBox'
import { useSearch } from '../hooks/useSearch'
import SearchBar from '../components/SearchBar'
import EntityFormModal from '../components/EntityFormModal'
import { CATEGORY_COLORS } from '../utils/categories'
import { useUrlState, useUrlModal } from '../hooks/useUrlState'
import { useDebouncedValue } from '../hooks/useDebouncedValue'
import { useLocalStorage } from '../hooks/useLocalStorage'
import { useTransientFlag } from '../hooks/useTransientFlag'

// ── Dictionary Lookup Modal ──────────────────────────────────────────
function DictModal({ query, data, loading, error, position, onClose }) {
  const ref = useRef(null)

  useEffect(() => {
    const handler = (e) => {
      if (ref.current && !ref.current.contains(e.target)) onClose()
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [onClose])

  useEffect(() => {
    const handler = (e) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [onClose])

  const style = {}
  if (position) {
    style.position = 'fixed'
    style.left = Math.min(position.x, window.innerWidth - 420)
    style.top = Math.min(position.y + 8, window.innerHeight - 400)
    style.zIndex = 50
  }

  return (
    <div ref={ref} style={style}
      className="w-[400px] max-w-[90vw] max-h-[380px] overflow-y-auto bg-slate-900 border border-slate-700 rounded-lg shadow-2xl"
    >
      <div className="sticky top-0 bg-slate-900 border-b border-slate-800 px-4 py-2.5 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <BookOpen size={14} className="text-indigo-400" />
          <span className="text-base font-medium text-slate-100">{query}</span>
          {data?.exact?.[0]?.pinyin && (
            <span className="text-sm text-amber-400/90">{pinyinToMarked(data.exact[0].pinyin)}</span>
          )}
        </div>
        <button onClick={onClose} className="text-slate-500 hover:text-slate-300">
          <X size={14} />
        </button>
      </div>

      <div className="px-4 py-3 space-y-3 text-sm">
        {loading && (
          <div className="flex items-center gap-2 text-slate-400">
            <Loader2 size={14} className="animate-spin" /> Looking up...
          </div>
        )}
        {error && <p className="text-rose-400 text-xs">{error}</p>}
        {data?.exact?.length > 0 && (
          <div>
            {data.exact.map((entry, i) => (
              <DictEntry key={i} entry={entry} highlight />
            ))}
          </div>
        )}
        {data && data.exact?.length === 0 && !loading && (
          <p className="text-slate-500 text-xs italic">No exact match found.</p>
        )}
        {data?.characters?.length > 0 && data.characters[0]?.pinyin && (
          <div>
            <div className="text-xs text-slate-600 uppercase tracking-wider mb-1.5">
              Character breakdown
            </div>
            {data.characters.map((entry, i) => (
              <DictEntry key={i} entry={entry} />
            ))}
          </div>
        )}
        {data?.compounds?.length > 0 && (
          <div>
            <div className="text-xs text-slate-600 uppercase tracking-wider mb-1.5">
              Compound words ({data.compounds.length})
            </div>
            {data.compounds.map((entry, i) => (
              <DictEntry key={i} entry={entry} compact />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function DictEntry({ entry, highlight, compact }) {
  return (
    <div className={`${compact ? 'py-1' : 'py-1.5'} ${highlight ? '' : 'opacity-80'}`}>
      <div className="flex items-baseline gap-2 flex-wrap">
        {!compact && entry.traditional !== entry.simplified && (
          <span className="text-slate-500 text-xs">{entry.traditional}</span>
        )}
        <span className={`${highlight ? 'text-indigo-300' : 'text-slate-300'} ${compact ? 'text-xs' : 'text-sm'} font-medium`}>
          {entry.simplified}
        </span>
        <span className="text-amber-400/80 text-xs">{pinyinToMarked(entry.pinyin)}</span>
        <span className="text-slate-600 text-xs">{entry.pinyin}</span>
      </div>
      <div className="text-slate-400 text-xs mt-0.5 leading-relaxed">
        {entry.definitions.filter(Boolean).join('; ')}
      </div>
    </div>
  )
}


// ── Retranslation Modal ──────────────────────────────────────────────
function RetranslateModal({ chineseText, lineIndex, allLines, bookId, providers, onResult, onClose }) {
  const [model, setModel] = useLocalStorage('editor.retranslateModel', '')
  const [translating, setTranslating] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const ref = useRef(null)
  const isWholeLine = lineIndex != null

  const modelOptions = []
  if (providers) {
    for (const p of providers) {
      if (!p.has_key) continue
      for (const m of (p.models || [])) {
        modelOptions.push(`${p.name}:${m}`)
      }
      if (p.models?.length === 0 && p.default_model) {
        modelOptions.push(`${p.name}:${p.default_model}`)
      }
    }
  }

  useEffect(() => {
    const handler = (e) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [onClose])

  const handleTranslate = async () => {
    if (!model) return
    setTranslating(true)
    setError(null)
    setResult(null)
    try {
      const idx = lineIndex ?? 0
      const contextBefore = allLines.slice(Math.max(0, idx - 3), idx)
      const contextAfter = allLines.slice(idx + 1, idx + 4)
      const res = await api.retranslate({
        text: chineseText,
        context_before: contextBefore,
        context_after: contextAfter,
        model,
        book_id: bookId ? parseInt(bookId) : null,
      })
      setResult(res.translation)
      onResult(chineseText, res.translation, lineIndex)
    } catch (e) {
      setError(e.message)
    } finally {
      setTranslating(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div ref={ref} className="bg-slate-900 border border-slate-700 rounded-lg shadow-2xl w-[500px] max-w-[90vw]">
        <div className="flex items-center justify-between px-4 py-3 border-b border-slate-800">
          <div className="flex items-center gap-2">
            <Languages size={16} className="text-emerald-400" />
            <span className="text-sm font-medium text-slate-200">
              {isWholeLine ? `Retranslate Line ${lineIndex + 1}` : 'Retranslate Selection'}
            </span>
          </div>
          <button onClick={onClose} className="text-slate-500 hover:text-slate-300">
            <X size={14} />
          </button>
        </div>
        <div className="px-4 py-3 space-y-3">
          <div>
            <div className="text-xs text-slate-600 uppercase tracking-wider mb-1">Source</div>
            <div className="text-sm text-slate-300 bg-slate-950 rounded px-3 py-2 font-mono break-all max-h-24 overflow-y-auto">
              {chineseText}
            </div>
          </div>
          <div>
            <label className="text-xs text-slate-600 uppercase tracking-wider block mb-1">Model</label>
            <ComboBox
              value={model}
              onChange={setModel}
              options={modelOptions}
              placeholder="Select a model..."
            />
          </div>
          <button
            className="btn-primary w-full flex items-center justify-center gap-2"
            onClick={handleTranslate}
            disabled={translating || !model}
          >
            {translating ? (
              <><Loader2 size={14} className="animate-spin" /> Translating...</>
            ) : (
              <><Languages size={14} /> Translate</>
            )}
          </button>
          {error && <p className="text-rose-400 text-xs">{error}</p>}
          {result && (
            <div>
              <div className="text-xs text-slate-600 uppercase tracking-wider mb-1">Result</div>
              <div className="text-sm text-emerald-300 bg-slate-950 rounded px-3 py-2 leading-relaxed">
                {result}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}


function PronounRepairModal({ bookId, chapterNumber, onResult, onClose }) {
  const [loading, setLoading] = useState(true)
  const [entities, setEntities] = useState([])
  const [entityId, setEntityId] = useState('')
  const [running, setRunning] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true); setError(null)
    api.listChapterGenderedEntities(bookId, chapterNumber)
      .then(d => { if (!cancelled) { setEntities(d.entities || []); setLoading(false) } })
      .catch(e => { if (!cancelled) { setError(e.message); setLoading(false) } })
    return () => { cancelled = true }
  }, [bookId, chapterNumber])

  const handleRun = async () => {
    if (!entityId) return
    setRunning(true); setError(null)
    try {
      const res = await api.pronounRepairChapter(bookId, chapterNumber, parseInt(entityId, 10))
      onResult(res)
    } catch (e) {
      setError(e.message)
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="card w-full max-w-md p-6 space-y-4 shadow-2xl">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Sparkles size={16} className="text-emerald-400" />
            <h2 className="font-semibold text-slate-200">Repair pronouns in this chapter</h2>
          </div>
          <button onClick={onClose} className="text-slate-500 hover:text-slate-300">
            <X size={16} />
          </button>
        </div>

        <p className="text-sm text-slate-400">
          Pick a character whose pronouns are wrong in this chapter. A small model will scan
          paragraphs mentioning them and rewrite the pronouns to match the character&rsquo;s
          recorded gender. The entity record itself is not changed.
        </p>

        {loading ? (
          <div className="flex items-center gap-2 text-slate-400 text-sm py-2">
            <Loader2 size={14} className="animate-spin" /> Loading characters…
          </div>
        ) : entities.length === 0 ? (
          <p className="text-sm text-amber-400">
            No character entities with a defined gender appear in this chapter&rsquo;s translated text.
            Set the gender on the relevant entity first, or check that the character&rsquo;s English name
            is actually present in the translation.
          </p>
        ) : (
          <div>
            <label className="label">Character ({entities.length} in this chapter)</label>
            <select
              className="input text-sm"
              value={entityId}
              onChange={e => setEntityId(e.target.value)}
              disabled={running}
            >
              <option value="">— Pick a character —</option>
              {entities.map(e => (
                <option key={e.entity_id} value={e.entity_id}>
                  {e.translation} ({e.gender}){e.untranslated ? ` — ${e.untranslated}` : ''}
                </option>
              ))}
            </select>
          </div>
        )}

        {error && <p className="text-rose-400 text-sm">{error}</p>}

        <div className="flex justify-end gap-2">
          <button className="btn-secondary" onClick={onClose} disabled={running}>Cancel</button>
          <button
            className="btn-primary flex items-center gap-1.5"
            onClick={handleRun}
            disabled={running || !entityId || entities.length === 0}
          >
            {running ? <Loader2 size={13} className="animate-spin" /> : <Sparkles size={13} />}
            {running ? 'Repairing…' : 'Run repair'}
          </button>
        </div>
      </div>
    </div>
  )
}


// ── Floating toolbar that appears on Chinese text selection ───────────
function SelectionToolbar({ position, onLookup, onRetranslate }) {
  if (!position) return null
  return (
    <div
      className="fixed z-40 bg-slate-800 border border-slate-700 rounded-md shadow-lg
                 flex items-center gap-0.5 p-0.5"
      style={{
        left: Math.min(position.x, window.innerWidth - 200),
        top: position.y - 36,
      }}
    >
      <button
        className="px-2.5 py-1 text-xs text-slate-300 hover:text-white hover:bg-slate-700
                   rounded flex items-center gap-1.5"
        onMouseDown={(e) => e.preventDefault()}
        onClick={onLookup}
      >
        <BookOpen size={12} /> Dictionary
      </button>
      <button
        className="px-2.5 py-1 text-xs text-slate-300 hover:text-white hover:bg-slate-700
                   rounded flex items-center gap-1.5"
        onMouseDown={(e) => e.preventDefault()}
        onClick={onRetranslate}
      >
        <Languages size={12} /> Retranslate
      </button>
    </div>
  )
}


// ── Highlighted Chinese line component ───────────────────────────────
const HighlightedChineseLine = memo(function HighlightedChineseLine({ line, matcher, annotation, onEntityClick, searchMatches, activeMatch }) {
  const segments = useMemo(
    () => highlightSegments(line, matcher, false),
    [line, matcher]
  )

  const content = segments.map((seg, j) => {
    if (seg.entity) {
      const colors = CATEGORY_COLORS[seg.entity.category] || CATEGORY_COLORS.characters
      return (
        <span
          key={j}
          title={`${seg.entity.translation} (${seg.entity.category}) — click to edit`}
          className="cursor-pointer rounded-sm hover:brightness-150 transition-all"
          style={{
            backgroundColor: colors.bg,
            borderBottom: `1px dashed ${colors.border}`,
          }}
          onClick={(e) => {
            e.stopPropagation()
            onEntityClick?.(seg.entity)
          }}
        >
          {seg.text}
        </span>
      )
    }
    return <span key={j}>{seg.text}</span>
  })

  // Overlay search marks on the whole line (simpler, more reliable approach)
  if (searchMatches && searchMatches.length > 0) {
    const parts = applySearchHighlights(line, searchMatches, activeMatch)
    const searchContent = parts.map((p, j) => {
      if (p.search) {
        return (
          <mark
            key={`s${j}`}
            style={{
              backgroundColor: p.active ? '#f59e0b' : '#fbbf24',
              color: '#1e293b',
              borderRadius: '1px',
              padding: '0 1px',
            }}
          >
            {p.text}
          </mark>
        )
      }
      // For non-search parts, render with entity highlighting
      return <span key={`s${j}`}>{p.text}</span>
    })

    if (annotation) {
      return (
        <ruby className="ruby-annotation">
          {searchContent}
          <rt className="text-emerald-400/90 font-sans text-[0.65em] leading-tight tracking-normal">
            {annotation}
          </rt>
        </ruby>
      )
    }
    return <>{searchContent}</>
  }

  if (annotation) {
    return (
      <ruby className="ruby-annotation">
        {content}
        <rt className="text-emerald-400/90 font-sans text-[0.65em] leading-tight tracking-normal">
          {annotation}
        </rt>
      </ruby>
    )
  }
  return <>{content}</>
})


// ── English overlay backdrop component ───────────────────────────────
const EnglishBackdrop = memo(function EnglishBackdrop({ text, textLines, matcher, scrollTop, paddingClass, searchMatches, activeMatch }) {
  const ref = useRef(null)

  const segments = useMemo(
    () => highlightSegments(text, matcher, true),
    [text, matcher]
  )

  // Sync scroll position with the textarea
  useEffect(() => {
    if (ref.current) ref.current.scrollTop = scrollTop
  }, [scrollTop])

  // Build search highlights per line
  const searchByLine = useMemo(() => {
    if (!searchMatches || Object.keys(searchMatches).length === 0) return null
    return searchMatches
  }, [searchMatches])

  // If we have search matches, render line-by-line with search highlights
  const hasSearchMatches = searchByLine && Object.keys(searchByLine).length > 0

  return (
    <div
      ref={ref}
      className={`absolute inset-0 pointer-events-none overflow-hidden
                 font-mono text-sm leading-relaxed whitespace-pre-wrap
                 ${paddingClass}`}
      style={{ overflowWrap: 'break-word', wordBreak: 'break-word' }}
    >
      {hasSearchMatches ? (
        // Line-by-line rendering with search highlights
        textLines.map((line, lineIdx) => {
          const lineMatches = searchByLine[lineIdx]
          if (lineMatches && lineMatches.length > 0) {
            const parts = applySearchHighlights(line, lineMatches, activeMatch)
            return (
              <span key={lineIdx}>
                {parts.map((p, j) => {
                  if (p.search) {
                    return (
                      <span
                        key={j}
                        style={{
                          backgroundColor: p.active ? '#f59e0b' : '#fbbf24',
                          borderRadius: '1px',
                        }}
                      >
                        <span style={{ color: 'transparent' }}>{p.text}</span>
                      </span>
                    )
                  }
                  return <span key={j} style={{ color: 'transparent' }}>{p.text}</span>
                })}
                {lineIdx < textLines.length - 1 ? '\n' : ''}
              </span>
            )
          }
          return <span key={lineIdx} style={{ color: 'transparent' }}>{line}{lineIdx < textLines.length - 1 ? '\n' : ''}</span>
        })
      ) : (
        // Original entity-only rendering
        segments.map((seg, i) => {
          if (seg.entity) {
            const colors = CATEGORY_COLORS[seg.entity.category] || CATEGORY_COLORS.characters
            return (
              <span
                key={i}
                style={{
                  backgroundColor: colors.bg,
                  borderBottom: `1px dashed ${colors.border}`,
                  borderRadius: '2px',
                }}
              >
                <span style={{ color: 'transparent' }}>{seg.text}</span>
              </span>
            )
          }
          return <span key={i} style={{ color: 'transparent' }}>{seg.text}</span>
        })
      )}
    </div>
  )
})


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
  // Local draft recovery — while dirty, the edited text+title is autosaved to
  // localStorage (debounced); on mount a newer-than-saved draft is offered
  // back via a restore banner. Survives session expiry, crashes, and
  // accidental navigation.
  const draftKey = `chapterDraft:${bookId}:${chapterNum}`
  const [draftOffer, setDraftOffer] = useState(null) // { text, title, savedAt } or null
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
  const [isProofread, setIsProofread] = useState(false)
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
    // If the chapter content changed, reload to show the fix
    if (res?.paragraphs_changed > 0) {
      api.getChapter(parseInt(bookId), parseInt(chapterNum))
        .then(ch => {
          if (Array.isArray(ch.content)) setText(ch.content.join('\n'))
        })
        .catch(() => {})
    }
  }, [bookId, chapterNum, flashPronounToast])

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

  // Overlay scroll sync
  const [overlayScrollTop, setOverlayScrollTop] = useState(0)

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
    setDraftOffer(null)
    Promise.all([
      api.getChapter(parseInt(bookId), parseInt(chapterNum)),
      api.getBook(parseInt(bookId)),
      api.listProviders(),
      api.listEntities({ book_id: parseInt(bookId), include_global: true }),
      api.listChapters(parseInt(bookId)),
    ])
      .then(([ch, bk, prov, ents, chaps]) => {
        setChapter(ch)
        setBook(bk)
        setProviders(prov.providers || [])
        setEntities(ents.entities || [])
        setChapterList((chaps.chapters || []).map(c => c.chapter).sort((a, b) => a - b))
        setIsProofread(!!ch.is_proofread)
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
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
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

  useEffect(() => {
    const handler = (e) => {
      if (dirty) {
        e.preventDefault()
        e.returnValue = ''
      }
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [dirty])

  // In-app navigation guard (data router). Only blocks when leaving this
  // editor path with unsaved changes — same-path query-param changes
  // (active line, modals, search) are never blocked.
  const shouldBlock = useCallback(
    ({ currentLocation, nextLocation }) =>
      dirty && currentLocation.pathname !== nextLocation.pathname,
    [dirty]
  )
  const blocker = useBlocker(shouldBlock)
  useEffect(() => {
    if (blocker.state === 'blocked') {
      if (window.confirm('Discard unsaved changes?')) blocker.proceed()
      else blocker.reset()
    }
  }, [blocker])

  // Draft autosave — while dirty, persist edited text + title (debounced 2s)
  // so session expiry or an accidental exit can be recovered.
  const chapterTitle = chapter?.title ?? null
  useEffect(() => {
    if (!dirty) return
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
  }, [dirty, text, chapterTitle, draftKey])

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
    mirror.style.width = ta.clientWidth + 'px'
    const lines = textLinesRef.current
    const heights = []
    mirror.textContent = ''
    for (const line of lines) {
      const span = document.createElement('div')
      span.textContent = line || '\u00A0'
      mirror.appendChild(span)
      heights.push(span.offsetHeight)
      mirror.removeChild(span)
    }
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
    const newText = search.replaceCurrentMatch(text, match)
    setText(newText)
    setDirty(true)
    clearSaved()
    // Advance to next match
    setTimeout(() => handleSearchNext(), 10)
  }, [search, text, handleSearchNext, clearSaved])

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
    if (search.isBookWide) {
      var totalBookMatches = search.bookMatchOrder.length
      if (!confirm('Replace all ' + totalBookMatches + ' matches across the entire book?')) return
      // Replace in current chapter locally
      var newText = search.replaceAllInChapter(text)
      if (newText !== text) {
        setText(newText)
        setDirty(true)
        clearSaved()
      }
      // Replace in other chapters via API
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
      flashUndoInfo({ type: 'book', prevText: prevText, count: totalBookMatches })
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
  }, [search, text, bookId, chapterNum, flashUndoInfo, clearSaved])

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
      setOverlayScrollTop(ta.scrollTop)
    }
  }, [currentChapterActiveMatch, search.isOpen])

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

  const handleSave = async () => {
    setSaving(true)
    setError(null)
    try {
      const payload = { content: textLines }
      if (chapter && chapter.title !== undefined) {
        payload.title = chapter.title
      }
      await api.updateChapter(parseInt(bookId), parseInt(chapterNum), payload)
      flashSaved()
      setDirty(false)
      localStorage.removeItem(draftKey)  // saved — draft no longer needed
      setDraftOffer(null)
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  const toggleProofread = async () => {
    try {
      const res = await api.setProofread(parseInt(bookId), parseInt(chapterNum), !isProofread)
      setIsProofread(res.is_proofread)
    } catch (e) {
      setError(e.message)
    }
  }

  const handleWpPublish = async () => {
    if (dirty) {
      if (!confirm('You have unsaved changes. Save and publish?')) return
      await handleSave()
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
    if (ta) setOverlayScrollTop(ta.scrollTop)

    if (scrollSyncSource.current === 'chinese') return
    scrollSyncSource.current = 'english'
    const ch = chineseRef.current
    if (!ta || !ch) return
    const scrollRatio = ta.scrollTop / (ta.scrollHeight - ta.clientHeight || 1)
    ch.scrollTop = scrollRatio * (ch.scrollHeight - ch.clientHeight || 1)
    requestAnimationFrame(() => { scrollSyncSource.current = null })
  }, [])

  const handleChineseScroll = useCallback(() => {
    if (scrollSyncSource.current === 'english') return
    scrollSyncSource.current = 'chinese'
    const ta = textareaRef.current
    const ch = chineseRef.current
    if (!ta || !ch) return
    const scrollRatio = ch.scrollTop / (ch.scrollHeight - ch.clientHeight || 1)
    ta.scrollTop = scrollRatio * (ta.scrollHeight - ta.clientHeight || 1)
    if (ta) setOverlayScrollTop(ta.scrollTop)
    requestAnimationFrame(() => { scrollSyncSource.current = null })
  }, [])

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

        {/* Source panel toggle */}
        {hasSource && (
          <button
            className={`text-xs px-2 py-1 rounded border transition-colors flex items-center gap-1 ${
              showSource
                ? 'border-sky-500/50 bg-sky-500/10 text-sky-300'
                : 'border-slate-700 text-slate-500 hover:text-slate-400'
            }`}
            onClick={() => setShowSource(!showSource)}
            title={showSource ? 'Hide Chinese source' : 'Show Chinese source'}
          >
            <Languages size={12} />
            {showSource ? 'Source' : 'Source off'}
          </button>
        )}

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
            isProofread
              ? 'border-emerald-500/50 bg-emerald-500/10 text-emerald-300'
              : 'border-slate-700 text-slate-500 hover:text-slate-400'
          }`}
          onClick={toggleProofread}
          title={isProofread ? `Proofread ${new Date(isProofread).toLocaleDateString()}` : 'Mark as proofread'}
        >
          <CheckCircle2 size={12} />
          {isProofread ? 'Proofread' : 'Not proofread'}
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
                  <div key={i} dangerouslySetInnerHTML={{ __html: renderBlock(seg.md) }} />
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
              <div style={{ transform: `translateY(-${overlayScrollTop}px)` }}>
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
                <EnglishBackdrop
                  text={debouncedText}
                  textLines={debouncedTextLines}
                  matcher={showEntities ? englishMatcher : emptyMatcher}
                  scrollTop={overlayScrollTop}
                  paddingClass={hasSource && showSource ? 'pr-4 pt-3 pb-4' : 'pr-5 pt-5 pb-5'}
                  searchMatches={translatedSearchMatches}
                  activeMatch={currentChapterActiveMatch?.field === 'translated' ? currentChapterActiveMatch : null}
                />
              )}
              <textarea
                ref={textareaRef}
                className={`absolute inset-0 w-full h-full text-slate-100 font-mono text-sm leading-relaxed
                           resize-none outline-none border-0
                           selection:bg-indigo-600/40 ${hasSource && showSource ? 'pr-4 pt-3 pb-4' : 'pr-5 pt-5 pb-5'}`}
                style={{
                  background: showEntities && englishMatcher.list.length > 0 ? 'transparent' : undefined,
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
