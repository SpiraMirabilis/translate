import { useState, useEffect, useRef, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../services/api'
import { Search, X, Loader2, BookOpen, ChevronRight } from 'lucide-react'

export default function GlobalSearchModal({ books, onClose }) {
  var [query, setQuery] = useState('')
  var [scope, setScope] = useState('translated')
  var [isRegex, setIsRegex] = useState(false)
  var [selectedBook, setSelectedBook] = useState(books.length === 1 ? books[0].id : null)
  var [results, setResults] = useState(null)
  var [loading, setLoading] = useState(false)
  var [totalMatches, setTotalMatches] = useState(0)

  var inputRef = useRef(null)
  var debounceRef = useRef(null)
  var navigate = useNavigate()

  useEffect(function focusOnMount() {
    if (inputRef.current) inputRef.current.focus()
  }, [])

  // Close on Escape
  useEffect(function escHandler() {
    function onKey(e) {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return function cleanup() { window.removeEventListener('keydown', onKey) }
  }, [onClose])

  var doSearch = useCallback(function performSearch(q, bookId, sc, regex) {
    if (!q || !bookId) {
      setResults(null)
      setTotalMatches(0)
      return
    }
    setLoading(true)
    api.searchBook(bookId, { query: q, scope: sc, is_regex: regex })
      .then(function onResult(res) {
        setResults(res.results || [])
        setTotalMatches(res.total_matches || 0)
      })
      .catch(function onErr() {
        setResults([])
        setTotalMatches(0)
      })
      .finally(function done() { setLoading(false) })
  }, [])

  // Debounced search on query/book/scope/regex change
  useEffect(function triggerSearch() {
    clearTimeout(debounceRef.current)
    if (!query || !selectedBook) {
      setResults(null)
      setTotalMatches(0)
      return
    }
    debounceRef.current = setTimeout(function fire() {
      doSearch(query, selectedBook, scope, isRegex)
    }, 300)
    return function cleanup() { clearTimeout(debounceRef.current) }
  }, [query, selectedBook, scope, isRegex, doSearch])

  function handleChapterClick(chapterNum) {
    var params = new URLSearchParams()
    params.set('search', query)
    params.set('searchScope', scope)
    if (isRegex) params.set('searchRegex', '1')
    params.set('searchBook', '1')
    navigate('/books/' + selectedBook + '/chapters/' + chapterNum + '/edit?' + params.toString())
    onClose()
  }

  var bookTitle = ''
  if (selectedBook) {
    var found = books.find(function findBook(b) { return b.id === selectedBook })
    if (found) bookTitle = found.title
  }

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center pt-[10vh] bg-black/60"
         onClick={function backdropClick(e) { if (e.target === e.currentTarget) onClose() }}>
      <div className="card w-full max-w-2xl max-h-[75vh] flex flex-col shadow-2xl">
        {/* Header */}
        <div className="flex items-center gap-3 px-4 py-3 border-b border-slate-700 shrink-0">
          <Search size={16} className="text-slate-500 shrink-0" />
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={function onInput(e) { setQuery(e.target.value) }}
            placeholder="Search across chapters..."
            className="flex-1 bg-transparent text-slate-200 placeholder-slate-600
                       outline-none text-sm"
          />
          <button className="btn-ghost p-1" onClick={onClose}><X size={16} /></button>
        </div>

        {/* Controls */}
        <div className="flex items-center gap-2 px-4 py-2 border-b border-slate-800 text-xs shrink-0">
          {/* Book selector */}
          {books.length > 1 && (
            <select
              value={selectedBook || ''}
              onChange={function onBook(e) { setSelectedBook(parseInt(e.target.value) || null) }}
              className="bg-slate-800 border border-slate-700 rounded px-2 py-1 text-slate-300
                         focus:outline-none focus:border-indigo-500/50"
            >
              <option value="">Select book...</option>
              {books.map(function renderOpt(b) {
                return <option key={b.id} value={b.id}>{b.title}</option>
              })}
            </select>
          )}

          {/* Scope */}
          <select
            value={scope}
            onChange={function onScope(e) { setScope(e.target.value) }}
            className="bg-slate-800 border border-slate-700 rounded px-1.5 py-1 text-slate-300
                       focus:outline-none focus:border-indigo-500/50"
          >
            <option value="translated">Translated</option>
            <option value="untranslated">Source</option>
            <option value="both">Both</option>
          </select>

          {/* Regex toggle */}
          <button
            className={'px-1.5 py-1 rounded border font-mono transition-colors ' + (
              isRegex
                ? 'border-indigo-500/50 bg-indigo-500/20 text-indigo-300'
                : 'border-slate-700 text-slate-500 hover:text-slate-400'
            )}
            onClick={function toggleRegex() { setIsRegex(!isRegex) }}
            title="Toggle regex mode"
          >
            .*
          </button>

          {/* Result count */}
          <span className="ml-auto text-slate-500 tabular-nums">
            {loading ? 'Searching...'
              : results !== null
                ? totalMatches + ' match' + (totalMatches !== 1 ? 'es' : '') +
                  ' in ' + results.length + ' chapter' + (results.length !== 1 ? 's' : '')
                : ''
            }
          </span>
        </div>

        {/* Results */}
        <div className="flex-1 overflow-auto">
          {!selectedBook && books.length > 1 && (
            <div className="p-8 text-center text-slate-500 text-sm">
              Select a book to search
            </div>
          )}

          {loading && (
            <div className="flex items-center justify-center p-8 text-slate-400 text-sm">
              <Loader2 size={14} className="animate-spin mr-2" /> Searching...
            </div>
          )}

          {!loading && results !== null && results.length === 0 && query && (
            <div className="p-8 text-center text-slate-500 text-sm">
              No matches found{bookTitle ? ' in ' + bookTitle : ''}
            </div>
          )}

          {!loading && results !== null && results.length > 0 && (
            <div className="divide-y divide-slate-800">
              {results.map(function renderResult(ch) {
                // Group matches by field for a brief summary
                var transCount = ch.matches.filter(function isTrans(m) { return m.field === 'translated' }).length
                var srcCount = ch.matches.filter(function isSrc(m) { return m.field === 'untranslated' }).length

                return (
                  <button
                    key={ch.chapter_number}
                    className="w-full text-left px-4 py-3 hover:bg-slate-800/60 transition-colors
                               flex items-center gap-3 group"
                    onClick={function go() { handleChapterClick(ch.chapter_number) }}
                  >
                    <div className="shrink-0 w-8 h-8 rounded bg-slate-800 flex items-center justify-center
                                    text-xs text-slate-400 font-medium">
                      {ch.chapter_number}
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="text-sm text-slate-200 truncate">
                        {ch.title}
                      </div>
                      <div className="text-xs text-slate-500 mt-0.5">
                        {ch.match_count} match{ch.match_count !== 1 ? 'es' : ''}
                        {transCount > 0 && srcCount > 0
                          ? ' (' + transCount + ' translated, ' + srcCount + ' source)'
                          : transCount > 0
                            ? ' in translation'
                            : ' in source'
                        }
                      </div>
                    </div>
                    <ChevronRight size={14} className="text-slate-600 group-hover:text-slate-400 shrink-0" />
                  </button>
                )
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
