import { useState, useRef, useEffect, useCallback } from 'react'
import { X, Search, Loader2, ChevronUp, ChevronDown } from 'lucide-react'

function highlightText(text, query, maxLen = 120) {
  if (!text || !query) return text

  // Find first match to center the snippet around it
  const lower = text.toLowerCase()
  const qLower = query.toLowerCase()
  const firstIdx = lower.indexOf(qLower)
  if (firstIdx === -1) return text.length > maxLen ? text.slice(0, maxLen) + '…' : text

  // Trim around first match if line is long
  let trimStart = 0
  let trimEnd = text.length
  if (text.length > maxLen) {
    trimStart = Math.max(0, firstIdx - Math.floor(maxLen / 3))
    trimEnd = Math.min(text.length, trimStart + maxLen)
  }
  const snippet = (trimStart > 0 ? '…' : '') + text.slice(trimStart, trimEnd) + (trimEnd < text.length ? '…' : '')

  // Split snippet by matches and build highlighted spans
  const snippetLower = snippet.toLowerCase()
  const parts = []
  let cursor = 0
  let pos = 0
  while ((pos = snippetLower.indexOf(qLower, cursor)) !== -1) {
    if (pos > cursor) parts.push(<span key={`t${cursor}`}>{snippet.slice(cursor, pos)}</span>)
    parts.push(<strong key={`m${pos}`} className="font-semibold text-indigo-400">{snippet.slice(pos, pos + query.length)}</strong>)
    cursor = pos + query.length
  }
  if (cursor < snippet.length) parts.push(<span key="tail">{snippet.slice(cursor)}</span>)
  return parts
}

function dedupeMatchesByLine(matches) {
  const seen = new Set()
  const result = []
  for (const m of matches) {
    const key = `${m.field}:${m.line}`
    if (!seen.has(key)) {
      seen.add(key)
      result.push(m)
    }
  }
  return result
}

export default function ReaderSearch({ open, onClose, bookId, onNavigate, theme, api }) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState([])
  const [totalMatches, setTotalMatches] = useState(0)
  const [searching, setSearching] = useState(false)
  const [currentIdx, setCurrentIdx] = useState(-1)
  const inputRef = useRef(null)
  const resultRefs = useRef({})

  const isDark = theme === 'dark'
  const panelBg = isDark ? 'bg-slate-800' : 'bg-white'
  const borderColor = isDark ? 'border-slate-700' : 'border-stone-200'
  const textPrimary = isDark ? 'text-slate-100' : 'text-gray-900'
  const textSecondary = isDark ? 'text-slate-400' : 'text-gray-500'
  const hoverBg = isDark ? 'hover:bg-slate-700' : 'hover:bg-stone-100'
  const inputBg = isDark ? 'bg-slate-900 border-slate-600 text-slate-100 placeholder-slate-500'
    : 'bg-white border-stone-300 text-gray-900 placeholder-gray-400'

  useEffect(() => {
    if (open) {
      setTimeout(() => inputRef.current?.focus(), 50)
    } else {
      setQuery('')
      setResults([])
      setTotalMatches(0)
      setCurrentIdx(-1)
    }
  }, [open])

  // Scroll active result into view
  useEffect(() => {
    if (currentIdx >= 0 && resultRefs.current[currentIdx]) {
      resultRefs.current[currentIdx].scrollIntoView({ block: 'nearest', behavior: 'smooth' })
    }
  }, [currentIdx])

  const doSearch = useCallback(async (q) => {
    if (!q.trim()) {
      setResults([])
      setTotalMatches(0)
      setCurrentIdx(-1)
      return
    }
    setSearching(true)
    try {
      const data = await api.searchBook(bookId, { query: q, scope: 'translated' })
      setResults(data.results || [])
      setTotalMatches(data.total_matches || 0)
      setCurrentIdx(data.results?.length > 0 ? 0 : -1)
    } catch {
      setResults([])
      setTotalMatches(0)
    } finally {
      setSearching(false)
    }
  }, [bookId, api])

  const handleSubmit = (e) => {
    e.preventDefault()
    doSearch(query)
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Escape') {
      onClose()
    } else if (e.key === 'Enter' && e.shiftKey) {
      e.preventDefault()
      navResult(-1)
    } else if (e.key === 'Enter' && !e.shiftKey && results.length > 0 && currentIdx >= 0) {
      // If results exist and we already searched, navigate to current
      if (results.length > 0 && currentIdx >= 0) {
        const r = results[currentIdx]
        onNavigate(r.chapter_number)
        onClose()
      }
    }
  }

  const navResult = (dir) => {
    if (results.length === 0) return
    setCurrentIdx(prev => {
      const next = prev + dir
      if (next < 0) return results.length - 1
      if (next >= results.length) return 0
      return next
    })
  }

  if (!open) return null

  return (
    <>
      <div className="fixed inset-0 z-40 bg-black/40" onClick={onClose} />
      <div className={`fixed top-16 left-1/2 -translate-x-1/2 z-50 w-[520px] max-w-[92vw] ${panelBg} border ${borderColor} rounded-xl shadow-2xl flex flex-col max-h-[70vh]`}>
        {/* Search input */}
        <form onSubmit={handleSubmit} className={`p-3 border-b ${borderColor} flex items-center gap-2`}>
          <Search size={16} className={textSecondary} />
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Search book..."
            className={`flex-1 text-sm px-2 py-1.5 rounded border outline-none focus:ring-1 focus:ring-indigo-500 ${inputBg}`}
          />
          {searching && <Loader2 size={16} className="animate-spin text-indigo-400" />}
          {results.length > 0 && (
            <div className="flex items-center gap-1">
              <button type="button" onClick={() => navResult(-1)} className={`p-1 rounded ${hoverBg} ${textSecondary}`}>
                <ChevronUp size={14} />
              </button>
              <button type="button" onClick={() => navResult(1)} className={`p-1 rounded ${hoverBg} ${textSecondary}`}>
                <ChevronDown size={14} />
              </button>
            </div>
          )}
          <button type="button" onClick={onClose} className={`${textSecondary} p-1`}>
            <X size={16} />
          </button>
        </form>

        {/* Results */}
        {results.length > 0 && (
          <div className="overflow-y-auto flex-1">
            <div className={`px-3 py-1.5 text-xs ${textSecondary} border-b ${borderColor}`}>
              {totalMatches} match{totalMatches !== 1 ? 'es' : ''} in {results.length} chapter{results.length !== 1 ? 's' : ''}
            </div>
            {results.map((r, i) => (
              <button
                key={r.chapter_number}
                ref={el => resultRefs.current[i] = el}
                onClick={() => { onNavigate(r.chapter_number); onClose() }}
                className={`w-full text-left px-4 py-3 border-b ${borderColor} transition-colors
                  ${i === currentIdx
                    ? isDark ? 'bg-indigo-600/20' : 'bg-indigo-50'
                    : hoverBg}`}
              >
                <div className="flex items-center justify-between mb-1">
                  <span className={`text-sm font-medium ${textPrimary}`}>
                    Ch. {r.chapter_number}{r.title ? `: ${r.title}` : ''}
                  </span>
                  <span className={`text-xs ${textSecondary} shrink-0 ml-2`}>
                    {r.match_count} match{r.match_count !== 1 ? 'es' : ''}
                  </span>
                </div>
                {/* Show first few unique match lines */}
                {dedupeMatchesByLine(r.matches || []).slice(0, 3).map((m, j) => (
                  <p key={j} className={`text-xs ${textSecondary} mt-0.5 line-clamp-1`}>
                    {highlightText(m.text || '', query)}
                  </p>
                ))}
                {dedupeMatchesByLine(r.matches || []).length > 3 && (
                  <p className={`text-xs ${textSecondary} mt-0.5 italic`}>
                    +{dedupeMatchesByLine(r.matches || []).length - 3} more
                  </p>
                )}
              </button>
            ))}
          </div>
        )}

        {/* No results */}
        {!searching && query && results.length === 0 && totalMatches === 0 && (
          <div className={`p-6 text-center text-sm ${textSecondary}`}>
            No matches found
          </div>
        )}
      </div>
    </>
  )
}
