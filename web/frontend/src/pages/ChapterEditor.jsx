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
import { ArrowLeft, ChevronLeft, ChevronRight, Save, Loader2, Check, AlertCircle, X, BookOpen, Languages, Trash2, CheckCircle2, Search, Pencil, Globe } from 'lucide-react'
import ComboBox from '../components/ComboBox'
import { useSearch } from '../hooks/useSearch'
import SearchBar from '../components/SearchBar'
import DeleteEntityModal from '../components/DeleteEntityModal'

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
  const [showDeleteModal, setShowDeleteModal] = useState(false)
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

  const handleDelete = async (decase) => {
    setShowDeleteModal(false)
    setDeleting(true); setError(null)
    try {
      if (decase && entity.book_id && entity.translation && /^[A-Z]/.test(entity.translation)) {
        await api.decaseEntity({ translation: entity.translation, book_id: entity.book_id })
      }
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
              onClick={() => setShowDeleteModal(true)}
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
      {showDeleteModal && (
        <DeleteEntityModal
          entities={[entity]}
          onConfirm={handleDelete}
          onCancel={() => setShowDeleteModal(false)}
        />
      )}
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


// ── Apply search highlight marks to text ──────────────────────────────
function applySearchHighlights(textContent, searchMatches, activeMatch) {
  if (!searchMatches || searchMatches.length === 0) return [{ text: textContent }]
  // Sort by col ascending
  const sorted = [...searchMatches].sort((a, b) => a.col - b.col)
  const parts = []
  let pos = 0
  for (const m of sorted) {
    if (m.col > pos) parts.push({ text: textContent.slice(pos, m.col) })
    const isActive = activeMatch && m.col === activeMatch.col && m.length === activeMatch.length && m.field === activeMatch.field
    parts.push({ text: textContent.slice(m.col, m.col + m.length), search: true, active: isActive })
    pos = m.col + m.length
  }
  if (pos < textContent.length) parts.push({ text: textContent.slice(pos) })
  return parts
}


// ── Highlighted Chinese line component ───────────────────────────────
function HighlightedChineseLine({ line, matcher, annotation, onEntityClick, searchMatches, activeMatch }) {
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
}


// ── English overlay backdrop component ───────────────────────────────
function EnglishBackdrop({ text, matcher, scrollTop, paddingClass, searchMatches, activeMatch }) {
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
        text.split('\n').map((line, lineIdx) => {
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
                {lineIdx < text.split('\n').length - 1 ? '\n' : ''}
              </span>
            )
          }
          return <span key={lineIdx} style={{ color: 'transparent' }}>{line}{lineIdx < text.split('\n').length - 1 ? '\n' : ''}</span>
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
}


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
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState(null)
  const [dirty, setDirty] = useState(false)
  const [activeLine, setActiveLine] = useState(0)
  const [providers, setProviders] = useState(null)
  const [entities, setEntities] = useState([])
  const [showEntities, setShowEntities] = useLocalStorage('editor.showEntities', true)
  const [showSource, setShowSource] = useLocalStorage('editor.showSource', true)
  const [isProofread, setIsProofread] = useState(false)
  const [chapterList, setChapterList] = useState([])
  const [editingTitle, setEditingTitle] = useState(false)
  const [titleDraft, setTitleDraft] = useState('')

  // WordPress publish state
  const [wpPublishing, setWpPublishing] = useState(false)
  const [wpStatus, setWpStatus] = useState(null) // null | 'new' | 'published' | 'changed'
  const [wpMessage, setWpMessage] = useState(null) // { type: 'success'|'error', text }

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

  // Search
  const search = useSearch()

  // Replace-all undo state
  const [undoInfo, setUndoInfo] = useState(null) // { type: 'local'|'book', prevText?, count }
  const undoTimerRef = useRef(null)

  // Overlay scroll sync
  const [overlayScrollTop, setOverlayScrollTop] = useState(0)

  const textareaRef = useRef(null)
  const chineseRef = useRef(null)
  const mirrorRef = useRef(null)
  const lineRefs = useRef([])
  const scrollSyncSource = useRef(null)
  const pendingSelection = useRef(null)
  const [lineHeights, setLineHeights] = useState([])

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
        setText(trimEmptyLines(content).join('\n'))
        const untrans = Array.isArray(ch.untranslated) ? ch.untranslated : []
        const filtered = untrans.filter(l => !l.startsWith('#'))
        const skipped = filtered.length > 0 ? filtered.slice(1) : filtered
        setUntranslatedLines(trimEmptyLines(skipped))
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

  // Measure wrapped line heights for the gutter
  const measureLineHeights = useCallback(() => {
    const mirror = mirrorRef.current
    const ta = textareaRef.current
    if (!mirror || !ta) return
    // Match the textarea's content width
    mirror.style.width = ta.clientWidth + 'px'
    const lines = text.split('\n')
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
  }, [text])

  useEffect(() => {
    measureLineHeights()
    window.addEventListener('resize', measureLineHeights)
    return () => window.removeEventListener('resize', measureLineHeights)
  }, [measureLineHeights])

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
    setSaved(false)
    // Advance to next match
    setTimeout(() => handleSearchNext(), 10)
  }, [search, text, handleSearchNext])

  const showUndoToast = useCallback(function showUndo(info) {
    clearTimeout(undoTimerRef.current)
    setUndoInfo(info)
    undoTimerRef.current = setTimeout(function hideUndo() { setUndoInfo(null) }, 15000)
  }, [])

  const handleUndo = useCallback(async function doUndo() {
    if (!undoInfo) return
    clearTimeout(undoTimerRef.current)
    if (undoInfo.type === 'local') {
      // Restore local text
      if (undoInfo.prevText != null) {
        setText(undoInfo.prevText)
        setDirty(true)
        setSaved(false)
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
        setSaved(false)
      }
      search.searchBook(bookId, search.query, search.scope, search.isRegex)
    }
    setUndoInfo(null)
  }, [undoInfo, bookId, search])

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
        setSaved(false)
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
      showUndoToast({ type: 'book', prevText: prevText, count: totalBookMatches })
    } else {
      var replaced = search.replaceAllInChapter(text)
      if (replaced !== text) {
        setText(replaced)
        setDirty(true)
        setSaved(false)
        var chCount = search.chapterMatches.filter(function onlyTrans(m) { return m.field === 'translated' }).length
        showUndoToast({ type: 'local', prevText: prevText, count: chCount })
      }
    }
  }, [search, text, bookId, chapterNum, showUndoToast])

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
      const lines = text.split('\n')
      const lineHeight = ta.scrollHeight / Math.max(lines.length, 1)
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
    setSaved(false)
  }

  const handleSave = async () => {
    setSaving(true)
    setError(null)
    try {
      const lines = text.split('\n')
      const payload = { content: lines }
      if (chapter && chapter.title !== undefined) {
        payload.title = chapter.title
      }
      await api.updateChapter(parseInt(bookId), parseInt(chapterNum), payload)
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

  const handleWpPublish = async () => {
    if (dirty) {
      if (!confirm('You have unsaved changes. Save and publish?')) return
      await handleSave()
    }
    setWpPublishing(true)
    setWpMessage(null)
    try {
      const res = await api.wpPublishChapter(parseInt(bookId), parseInt(chapterNum))
      const actionText = res.action === 'created' ? 'Published' : res.action === 'updated' ? 'Updated' : 'Already up to date'
      setWpMessage({ type: 'success', text: actionText })
      setWpStatus('published')
      setTimeout(() => setWpMessage(null), 5000)
    } catch (e) {
      setWpMessage({ type: 'error', text: e.message })
      setTimeout(() => setWpMessage(null), 8000)
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

  // Prev / next chapter navigation
  const chapterIdx = chapterList.indexOf(parseInt(chapterNum))
  const prevChapter = chapterIdx > 0 ? chapterList[chapterIdx - 1] : null
  const nextChapter = chapterIdx >= 0 && chapterIdx < chapterList.length - 1 ? chapterList[chapterIdx + 1] : null

  const goToChapter = (num) => {
    if (dirty && !confirm('You have unsaved changes. Leave anyway?')) return
    navigate(`/books/${bookId}/chapters/${num}/edit`)
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
          onClick={() => {
            if (dirty && !confirm('You have unsaved changes. Leave anyway?')) return
            navigate('/books')
          }}
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
          <div className="text-sm font-medium text-slate-200 truncate">
            {book?.title} — Chapter {chapterNum}
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
          title={isProofread ? 'Marked as proofread' : 'Mark as proofread'}
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
              {((showEntities && englishMatcher.length > 0) || Object.keys(translatedSearchMatches).length > 0) && (
                <EnglishBackdrop
                  text={text}
                  matcher={showEntities ? englishMatcher : []}
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
            onClick={() => { clearTimeout(undoTimerRef.current); setUndoInfo(null) }}
          >
            <X size={14} />
          </button>
        </div>
      )}
    </div>
  )
}
