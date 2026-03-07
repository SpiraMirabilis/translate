/**
 * Shared dictionary lookup components.
 * Extracted from ChapterEditor for reuse in entity review/edit modals.
 */
import { useState, useCallback, useRef, useEffect } from 'react'
import { BookOpen, X, Loader2 } from 'lucide-react'
import { api } from '../services/api'

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

export function pinyinToMarked(pinyin) {
  const normalized = pinyin.replace(/u:/g, 'ü')
  return normalized.split(/\s+/).map(syllableToMarked).join(' ')
}

export function DictEntry({ entry, highlight, compact }) {
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

/**
 * Inline dictionary result panel (no positioning — rendered in document flow).
 * Use inside modals next to the untranslated field.
 */
export function DictResult({ data, loading, error, query, onClose }) {
  if (!query) return null

  return (
    <div className="bg-slate-900/70 border border-slate-700 rounded p-3 space-y-2">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <BookOpen size={13} className="text-indigo-400" />
          <span className="text-sm font-medium text-slate-100">{query}</span>
          {data?.exact?.[0]?.pinyin && (
            <span className="text-sm text-amber-400/90">{pinyinToMarked(data.exact[0].pinyin)}</span>
          )}
        </div>
        <button onClick={onClose} className="text-slate-500 hover:text-slate-300">
          <X size={12} />
        </button>
      </div>

      {loading && (
        <div className="flex items-center gap-2 text-slate-400 text-xs">
          <Loader2 size={12} className="animate-spin" /> Looking up...
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
          <div className="text-xs text-slate-600 uppercase tracking-wider mb-1">Character breakdown</div>
          {data.characters.map((entry, i) => (
            <DictEntry key={i} entry={entry} />
          ))}
        </div>
      )}
    </div>
  )
}

/**
 * Hook for dictionary lookup state management.
 */
export function useDictLookup() {
  const [dictQuery, setDictQuery] = useState(null)
  const [dictData, setDictData] = useState(null)
  const [dictLoading, setDictLoading] = useState(false)
  const [dictError, setDictError] = useState(null)

  const lookup = useCallback(async (text) => {
    const q = text.trim()
    if (!q) return
    setDictQuery(q)
    setDictData(null)
    setDictError(null)
    setDictLoading(true)
    try {
      const result = await api.dictLookup(q)
      setDictData(result)
    } catch (e) {
      setDictError(e.message)
    } finally {
      setDictLoading(false)
    }
  }, [])

  const close = useCallback(() => {
    setDictQuery(null)
    setDictData(null)
    setDictError(null)
  }, [])

  return { dictQuery, dictData, dictLoading, dictError, lookup, close }
}
