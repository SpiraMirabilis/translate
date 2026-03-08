/**
 * ChapterEditor — split-pane translation editor for a single chapter.
 * Left: read-only Chinese source text with line highlighting, dictionary lookup,
 *       inline retranslation annotations, and entity highlighting.
 * Right: editable English translation with synchronized scrolling and
 *        entity highlighting via overlay technique.
 * Reached via /books/:bookId/chapters/:chapterNum/edit
 */
import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import { api } from '../services/api'
import { ArrowLeft, Save, Loader2, Check, AlertCircle, X, BookOpen, Languages, Trash2, CheckCircle2 } from 'lucide-react'
import ComboBox from '../components/ComboBox'

const CATEGORIES = ['characters', 'places', 'organizations', 'abilities', 'titles', 'equipment', 'creatures']

// ── Trim empty lines from start/end of an array ─────────────────────
function trimEmptyLines(lines) {
  let start = 0
  while (start < lines.length && !lines[start].trim()) start++
  let end = lines.length
  while (end > start && !lines[end - 1].trim()) end--
  return lines.slice(start, end)
}

// ── localStorage helper ──────────────────────────────────────────────
function useLocalStorage(key, defaultValue) {
  const [value, setValue] = useState(() => {
    try { const v = localStorage.getItem(key); return v !== null ? JSON.parse(v) : defaultValue }
    catch { return defaultValue }
  })
  const set = useCallback((v) => {
    setValue(v)
    localStorage.setItem(key, JSON.stringify(v))
  }, [key])
  return [value, set]
}

// ── Pinyin tone number → tone mark conversion ───────────────────────
const TONE_MARKS = {
  a: ['ā','á','ǎ','à'], e: ['ē','é','ě','è'], i: ['ī','í','ǐ','ì'],
  o: ['ō','ó','ǒ','ò'], u: ['ū','ú','ǔ','ù'], ü: ['ǖ','ǘ','ǚ','ǜ'],
}

function syllableToMarked(s) {
  const m = s.match(/^([a-zA-ZüÜ]+?)([1-5])$/)
  if (!m) return s
  let [, base, tone] = m
  const wasCapitalized = base[0] === base[0].toUpperCase()
  base = base.toLowerCase()
  tone = parseInt(tone)
  if (tone === 5) return base
  const vowels = 'aeiouü'
  let idx = -1
  if (base.includes('a')) idx = base.indexOf('a')
  else if (base.includes('e')) idx = base.indexOf('e')
  else if (base.includes('ou')) idx = base.indexOf('o')
  else {
    for (let i = base.length - 1; i >= 0; i--) {
      if (vowels.includes(base[i])) { idx = i; break }
    }
  }
  if (idx === -1) return base
  const ch = base[idx]
  const marked = TONE_MARKS[ch]?.[tone - 1]
  if (!marked) return base
  let result = base.slice(0, idx) + marked + base.slice(idx + 1)
  if (wasCapitalized) result = result[0].toUpperCase() + result.slice(1)
  return result
}

function pinyinToMarked(pinyin) {
  const normalized = pinyin.replace(/u:/g, 'ü')
  return normalized.split(/\s+/).map(syllableToMarked).join(' ')
}


// ── Entity highlighting helpers ──────────────────────────────────────
// Category → color mapping for entity highlights
const CATEGORY_COLORS = {
  characters:    { bg: 'rgba(99,102,241,0.28)',  border: 'rgba(99,102,241,0.6)' },   // indigo
  places:        { bg: 'rgba(34,197,94,0.28)',   border: 'rgba(34,197,94,0.6)' },    // green
  organizations: { bg: 'rgba(249,115,22,0.28)',  border: 'rgba(249,115,22,0.6)' },   // orange
  abilities:     { bg: 'rgba(168,85,247,0.28)',  border: 'rgba(168,85,247,0.6)' },   // purple
  titles:        { bg: 'rgba(236,72,153,0.28)',  border: 'rgba(236,72,153,0.6)' },   // pink
  equipment:     { bg: 'rgba(234,179,8,0.28)',   border: 'rgba(234,179,8,0.6)' },    // yellow
  creatures:     { bg: 'rgba(6,182,212,0.28)',   border: 'rgba(6,182,212,0.6)' },    // cyan
}

/**
 * Build a sorted matcher list from entities.
 * Returns [{ text, translation, untranslated, category }] sorted by text length desc.
 * `field` is 'untranslated' for Chinese matching, 'translation' for English matching.
 */
function buildMatcher(entities, field) {
  const seen = new Set()
  const list = []
  for (const ent of entities) {
    const key = ent[field]
    if (!key || key.length < 2 || seen.has(key.toLowerCase())) continue
    seen.add(key.toLowerCase())
    list.push({
      text: key,
      lower: key.toLowerCase(),
      translation: ent.translation,
      untranslated: ent.untranslated,
      category: ent.category,
    })
  }
  // Sort by length descending so longest matches win
  list.sort((a, b) => b.text.length - a.text.length)
  return list
}

/**
 * Split a line into segments of plain text and entity matches.
 * Returns [{ text, entity? }] where entity is the matched entity info or null.
 * Uses case-insensitive matching for English, exact for Chinese.
 */
function highlightSegments(line, matcher, caseInsensitive = false) {
  if (!line || !matcher.length) return [{ text: line || '\u00A0' }]

  const segments = []
  let remaining = line
  let pos = 0

  while (remaining.length > 0) {
    let earliest = null
    let earliestIdx = remaining.length

    for (const m of matcher) {
      const idx = caseInsensitive
        ? remaining.toLowerCase().indexOf(m.lower)
        : remaining.indexOf(m.text)
      if (idx !== -1 && idx < earliestIdx) {
        earliestIdx = idx
        earliest = m
      }
      // Optimization: if we found a match at position 0 with the longest matcher, stop
      if (earliestIdx === 0) break
    }

    if (!earliest) {
      segments.push({ text: remaining })
      break
    }

    if (earliestIdx > 0) {
      segments.push({ text: remaining.slice(0, earliestIdx) })
    }

    const matchedText = remaining.slice(earliestIdx, earliestIdx + earliest.text.length)
    segments.push({ text: matchedText, entity: earliest })
    remaining = remaining.slice(earliestIdx + earliest.text.length)
  }

  return segments
}


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
      className="w-[400px] max-h-[380px] overflow-y-auto bg-slate-900 border border-slate-700 rounded-lg shadow-2xl"
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


// ── Inline Entity Edit Modal ─────────────────────────────────────────
function EntityEditModal({ entity, onClose, onSaved }) {
  const [form, setForm] = useState({
    category: entity.category,
    translation: entity.translation,
    gender: entity.gender || '',
    note: entity.note || '',
  })
  const [saving, setSaving] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    const handler = (e) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [onClose])

  const handleSave = async () => {
    if (!form.translation.trim()) { setError('Translation is required'); return }
    setSaving(true); setError(null)
    try {
      await api.updateEntity(entity.id, {
        category: form.category,
        translation: form.translation,
        gender: form.gender || null,
        note: form.note || null,
      })
      onSaved()
    } catch (e) {
      setError(e.message); setSaving(false)
    }
  }

  const handleDelete = async () => {
    if (!confirm(`Delete entity "${entity.untranslated}" (${entity.translation})?`)) return
    setDeleting(true); setError(null)
    try {
      await api.deleteEntity(entity.id)
      onSaved()
    } catch (e) {
      setError(e.message); setDeleting(false)
    }
  }

  const colors = CATEGORY_COLORS[entity.category] || CATEGORY_COLORS.characters

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="bg-slate-900 border border-slate-700 rounded-lg shadow-2xl w-[420px] max-w-[90vw]">
        <div className="flex items-center justify-between px-4 py-3 border-b border-slate-800">
          <div className="flex items-center gap-2">
            <span
              className="w-2.5 h-2.5 rounded-full"
              style={{ backgroundColor: colors.border }}
            />
            <span className="text-sm font-medium text-slate-200">Edit Entity</span>
          </div>
          <button onClick={onClose} className="text-slate-500 hover:text-slate-300">
            <X size={14} />
          </button>
        </div>

        <div className="px-4 py-3 space-y-3">
          {/* Untranslated (read-only) */}
          <div>
            <label className="text-xs text-slate-600 uppercase tracking-wider block mb-1">Chinese</label>
            <div className="text-sm text-slate-300 bg-slate-950 rounded px-3 py-2 font-mono">
              {entity.untranslated}
            </div>
          </div>

          {/* Translation */}
          <div>
            <label className="text-xs text-slate-600 uppercase tracking-wider block mb-1">Translation</label>
            <input
              className="w-full bg-slate-950 border border-slate-700 rounded px-3 py-1.5 text-sm text-slate-200
                         focus:outline-none focus:border-indigo-500"
              value={form.translation}
              onChange={e => setForm(f => ({ ...f, translation: e.target.value }))}
              autoFocus
            />
          </div>

          {/* Category */}
          <div>
            <label className="text-xs text-slate-600 uppercase tracking-wider block mb-1">Category</label>
            <select
              className="w-full bg-slate-950 border border-slate-700 rounded px-3 py-1.5 text-sm text-slate-200
                         focus:outline-none focus:border-indigo-500"
              value={form.category}
              onChange={e => setForm(f => ({ ...f, category: e.target.value }))}
            >
              {CATEGORIES.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>

          {/* Gender (characters only) */}
          {form.category === 'characters' && (
            <div>
              <label className="text-xs text-slate-600 uppercase tracking-wider block mb-1">Gender</label>
              <select
                className="w-full bg-slate-950 border border-slate-700 rounded px-3 py-1.5 text-sm text-slate-200
                           focus:outline-none focus:border-indigo-500"
                value={form.gender}
                onChange={e => setForm(f => ({ ...f, gender: e.target.value }))}
              >
                <option value="">Unknown</option>
                <option value="male">Male</option>
                <option value="female">Female</option>
                <option value="neutral">Neutral</option>
              </select>
            </div>
          )}

          {/* Note */}
          <div>
            <label className="text-xs text-slate-600 uppercase tracking-wider block mb-1">Note</label>
            <input
              className="w-full bg-slate-950 border border-slate-700 rounded px-3 py-1.5 text-sm text-slate-200
                         focus:outline-none focus:border-indigo-500"
              value={form.note}
              onChange={e => setForm(f => ({ ...f, note: e.target.value }))}
              placeholder="Translation guidance for AI"
            />
          </div>

          {/* Book scope indicator */}
          <div className="text-xs text-slate-600">
            {entity.book_id ? `Book-specific (book ${entity.book_id})` : 'Global entity'}
          </div>

          {error && <p className="text-rose-400 text-xs">{error}</p>}

          <div className="flex items-center justify-between pt-1">
            <button
              className="text-xs text-rose-400/70 hover:text-rose-400 flex items-center gap-1"
              onClick={handleDelete}
              disabled={deleting}
            >
              {deleting ? <Loader2 size={12} className="animate-spin" /> : <Trash2 size={12} />}
              Delete
            </button>
            <div className="flex gap-2">
              <button className="btn-secondary text-sm" onClick={onClose}>Cancel</button>
              <button
                className="btn-primary text-sm flex items-center gap-1.5"
                onClick={handleSave}
                disabled={saving}
              >
                {saving ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />}
                Save
              </button>
            </div>
          </div>
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
function HighlightedChineseLine({ line, matcher, annotation, onEntityClick }) {
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
}


// ── English overlay backdrop component ───────────────────────────────
function EnglishBackdrop({ text, matcher, scrollTop, paddingClass }) {
  const ref = useRef(null)
  const segments = useMemo(
    () => highlightSegments(text, matcher, true),
    [text, matcher]
  )

  // Sync scroll position with the textarea
  useEffect(() => {
    if (ref.current) ref.current.scrollTop = scrollTop
  }, [scrollTop])

  return (
    <div
      ref={ref}
      className={`absolute inset-0 pointer-events-none overflow-hidden
                 font-mono text-sm leading-relaxed whitespace-pre-wrap
                 ${paddingClass}`}
      style={{ overflowWrap: 'break-word', wordBreak: 'break-word' }}
    >
      {segments.map((seg, i) => {
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
              {/* Invisible text to take up the same space as the textarea text */}
              <span style={{ color: 'transparent' }}>{seg.text}</span>
            </span>
          )
        }
        return <span key={i} style={{ color: 'transparent' }}>{seg.text}</span>
      })}
    </div>
  )
}


// ── Main Component ───────────────────────────────────────────────────
export default function ChapterEditor() {
  const { bookId, chapterNum } = useParams()
  const navigate = useNavigate()

  const [chapter, setChapter] = useState(null)
  const [book, setBook] = useState(null)
  const [text, setText] = useState('')
  const [untranslatedLines, setUntranslatedLines] = useState([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState(null)
  const [dirty, setDirty] = useState(false)
  const [activeLine, setActiveLine] = useState(0)
  const [providers, setProviders] = useState(null)
  const [entities, setEntities] = useState([])
  const [showEntities, setShowEntities] = useLocalStorage('editor.showEntities', true)
  const [isProofread, setIsProofread] = useState(false)

  // Dictionary state
  const [dictQuery, setDictQuery] = useState(null)
  const [dictData, setDictData] = useState(null)
  const [dictLoading, setDictLoading] = useState(false)
  const [dictError, setDictError] = useState(null)
  const [dictPos, setDictPos] = useState(null)
  const [selToolbar, setSelToolbar] = useState(null)

  // Retranslation state
  const [retranslateModal, setRetranslateModal] = useState(null)
  const [annotations, setAnnotations] = useState({})
  const [editingEntity, setEditingEntity] = useState(null)

  // Overlay scroll sync
  const [overlayScrollTop, setOverlayScrollTop] = useState(0)

  const textareaRef = useRef(null)
  const chineseRef = useRef(null)
  const lineRefs = useRef([])
  const scrollSyncSource = useRef(null)
  const pendingSelection = useRef(null)

  // Build matchers from entities
  const chineseMatcher = useMemo(
    () => showEntities ? buildMatcher(entities, 'untranslated') : [],
    [entities, showEntities]
  )
  const englishMatcher = useMemo(
    () => showEntities ? buildMatcher(entities, 'translation') : [],
    [entities, showEntities]
  )

  useEffect(() => {
    Promise.all([
      api.getChapter(parseInt(bookId), parseInt(chapterNum)),
      api.getBook(parseInt(bookId)),
      api.listProviders(),
      api.listEntities({ book_id: parseInt(bookId), include_global: true }),
    ])
      .then(([ch, bk, prov, ents]) => {
        setChapter(ch)
        setBook(bk)
        setProviders(prov.providers || [])
        setEntities(ents.entities || [])
        setIsProofread(!!ch.is_proofread)
        const content = Array.isArray(ch.content) ? ch.content : []
        setText(trimEmptyLines(content).join('\n'))
        const untrans = Array.isArray(ch.untranslated) ? ch.untranslated : []
        const filtered = untrans.filter(l => !l.startsWith('#'))
        const skipped = filtered.length > 0 ? filtered.slice(1) : filtered
        setUntranslatedLines(trimEmptyLines(skipped))
      })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [bookId, chapterNum])

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

  const handleChange = (e) => {
    setText(e.target.value)
    setDirty(true)
    setSaved(false)
  }

  const handleSave = async () => {
    setSaving(true)
    setError(null)
    try {
      const lines = text.split('\n')
      await api.updateChapter(parseInt(bookId), parseInt(chapterNum), { content: lines })
      setSaved(true)
      setDirty(false)
      setTimeout(() => setSaved(false), 3000)
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

  const handleKeyDown = (e) => {
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

  const updateActiveLine = useCallback(() => {
    if (!textareaRef.current) return
    const pos = textareaRef.current.selectionStart
    const lineNum = text.substring(0, pos).split('\n').length - 1
    setActiveLine(lineNum)
  }, [text])

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
    setRetranslateModal({ text: selText, lineIndex })
  }, [])

  const handleRetranslateLine = useCallback((lineIndex) => {
    const line = untranslatedLines[lineIndex]
    if (!line) return
    setRetranslateModal({ text: line, lineIndex })
  }, [untranslatedLines])

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
    if (full) setEditingEntity(full)
  }, [entities])

  const handleEntitySaved = useCallback(() => {
    setEditingEntity(null)
    // Reload entities to reflect changes
    api.listEntities({ book_id: parseInt(bookId), include_global: true })
      .then(res => setEntities(res.entities || []))
      .catch(() => {})
  }, [bookId])

  const lineCount = text.split('\n').length
  const wordCount = text.trim() ? text.trim().split(/\s+/).length : 0
  const entityCount = entities.length

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
      <div className="flex items-center gap-3 px-5 py-3 border-b border-slate-800 bg-slate-900/50 shrink-0">
        <button
          className="btn-ghost p-1.5"
          onClick={() => {
            if (dirty && !confirm('You have unsaved changes. Leave anyway?')) return
            navigate('/books')
          }}
        >
          <ArrowLeft size={16} />
        </button>

        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium text-slate-200 truncate">
            {book?.title} — Chapter {chapterNum}
            {chapter?.title && chapter.title !== `Chapter ${chapterNum}` && (
              <span className="text-slate-500 ml-2">"{chapter.title}"</span>
            )}
          </div>
          <div className="text-xs text-slate-500 mt-0.5">
            {lineCount.toLocaleString()} lines · {wordCount.toLocaleString()} words
            {chapter?.model && <span> · {chapter.model}</span>}
            {dirty && <span className="text-amber-500 ml-2">· Unsaved changes</span>}
            {hasSource && (
              <span className="text-slate-600 ml-2">· Double-click to look up · Select to retranslate</span>
            )}
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
          title={isProofread ? 'Marked as proofread' : 'Mark as proofread'}
        >
          <CheckCircle2 size={12} />
          {isProofread ? 'Proofread' : 'Not proofread'}
        </button>

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

      {/* Split-pane editor */}
      <div className="flex-1 overflow-hidden flex relative">
        {/* Chinese source panel (left) */}
        {hasSource && (
          <div
            ref={chineseRef}
            onScroll={handleChineseScroll}
            onMouseUp={handleChineseMouseUp}
            onDoubleClick={handleChineseDblClick}
            className="w-1/2 overflow-y-auto bg-slate-950 border-r border-slate-800 select-text"
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
                          onClick={() => setEditingEntity(ent)}
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
        <div className={`${hasSource ? 'w-1/2' : 'flex-1'} flex flex-col overflow-hidden`}>
          {hasSource && (
            <div className="px-4 pt-4 pb-1">
              <div className="text-xs text-slate-600 uppercase tracking-wider font-medium">
                Translation (editable)
              </div>
            </div>
          )}
          <div className="flex-1 relative overflow-hidden bg-slate-950">
            {/* Backdrop: renders entity highlights behind the textarea */}
            {showEntities && englishMatcher.length > 0 && (
              <EnglishBackdrop
                text={text}
                matcher={englishMatcher}
                scrollTop={overlayScrollTop}
                paddingClass={hasSource ? 'p-4 pt-3' : 'p-5'}
              />
            )}
            <textarea
              ref={textareaRef}
              className={`absolute inset-0 w-full h-full text-slate-100 font-mono text-sm leading-relaxed
                         resize-none outline-none border-0
                         selection:bg-indigo-600/40 ${hasSource ? 'p-4 pt-3' : 'p-5'}`}
              style={{
                background: showEntities && englishMatcher.length > 0 ? 'transparent' : undefined,
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
          onClose={() => setRetranslateModal(null)}
        />
      )}

      {editingEntity && (
        <EntityEditModal
          entity={editingEntity}
          onClose={() => setEditingEntity(null)}
          onSaved={handleEntitySaved}
        />
      )}
    </div>
  )
}
