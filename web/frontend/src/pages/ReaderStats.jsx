import { useState, useEffect, useCallback } from 'react'
import { api } from '../services/api'
import {
  ChevronDown, ChevronRight, Loader2, RefreshCw, Globe, Server, Eye, Download, BookOpen
} from 'lucide-react'

const DURATION_RE = /^\d+\s*[dhm]$/i
const PRESETS = ['1h', '24h', '7d', '30d']
const GROUP_OPTIONS = [
  { id: 'ip',   label: 'By IP'   },
  { id: 'book', label: 'By book' },
]

export default function ReaderStats() {
  const [duration, setDuration] = useState('24h')
  const [groupBy, setGroupBy] = useState('ip')
  const [customInput, setCustomInput] = useState('')
  const [customError, setCustomError] = useState('')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [expanded, setExpanded] = useState(new Set())

  const load = useCallback(async (d, g) => {
    setLoading(true)
    setError('')
    try {
      const resp = await api.getReaderStats(d, g)
      setData(resp)
    } catch (e) {
      setError(e.message || 'Failed to load reader stats')
      setData(null)
    }
    setLoading(false)
  }, [])

  useEffect(() => { load(duration, groupBy) }, [duration, groupBy, load])
  useEffect(() => { setExpanded(new Set()) }, [groupBy])

  const toggle = (key) => {
    setExpanded(prev => {
      const next = new Set(prev)
      next.has(key) ? next.delete(key) : next.add(key)
      return next
    })
  }

  const applyCustom = () => {
    const v = customInput.trim().toLowerCase()
    if (!v) { setCustomError(''); return }
    if (!DURATION_RE.test(v)) {
      setCustomError('Use a format like 7d, 12h, 30m')
      return
    }
    setCustomError('')
    setDuration(v)
  }

  const activeGroup = data?.group_by || groupBy
  const entries = (activeGroup === 'ip' ? data?.ips : data?.books) || []
  const entryKey = (e) => activeGroup === 'ip' ? e.ip : `book-${e.book_id}`

  const expandAll = () => {
    if (!entries?.length) return
    setExpanded(new Set(entries.map(entryKey)))
  }
  const collapseAll = () => setExpanded(new Set())

  const isPresetActive = (p) => p === duration

  return (
    <div className="max-w-6xl mx-auto p-4 md:p-6">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-lg font-semibold text-slate-100">Reader Stats</h1>
          <p className="text-sm text-slate-400">
            {data
              ? <>
                  {data.total_views.toLocaleString()} view{data.total_views !== 1 ? 's' : ''} across{' '}
                  {data.unique_ips} unique IP{data.unique_ips !== 1 ? 's' : ''} &middot; window: {data.duration}
                </>
              : <>Loading activity…</>}
          </p>
        </div>
        <button
          className="btn-ghost flex items-center gap-1 text-xs"
          onClick={() => load(duration, groupBy)}
          disabled={loading}
          title="Refresh"
        >
          <RefreshCw size={12} className={loading ? 'animate-spin' : ''} /> Refresh
        </button>
      </div>

      {/* Controls */}
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <span className="text-xs text-slate-500 mr-1">Window:</span>
        {PRESETS.map(p => (
          <button
            key={p}
            onClick={() => { setCustomInput(''); setCustomError(''); setDuration(p) }}
            className={`px-2.5 py-1 text-xs rounded transition-colors ${
              isPresetActive(p)
                ? 'bg-indigo-600 text-white'
                : 'bg-slate-800 text-slate-300 hover:bg-slate-700'
            }`}
          >
            {p}
          </button>
        ))}
        <div className="flex items-center gap-1 ml-2">
          <input
            type="text"
            placeholder="custom (e.g. 3d)"
            value={customInput}
            onChange={(e) => { setCustomInput(e.target.value); setCustomError('') }}
            onKeyDown={(e) => { if (e.key === 'Enter') applyCustom() }}
            onBlur={applyCustom}
            className="input text-xs py-1 px-2 w-32"
          />
          {customError && (
            <span className="text-xs text-rose-400">{customError}</span>
          )}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2 mb-4">
        <span className="text-xs text-slate-500 mr-1">Group:</span>
        {GROUP_OPTIONS.map(opt => (
          <button
            key={opt.id}
            onClick={() => setGroupBy(opt.id)}
            className={`px-2.5 py-1 text-xs rounded transition-colors ${
              groupBy === opt.id
                ? 'bg-indigo-600 text-white'
                : 'bg-slate-800 text-slate-300 hover:bg-slate-700'
            }`}
          >
            {opt.label}
          </button>
        ))}
        {entries.length > 0 && (
          <div className="ml-auto flex items-center gap-2">
            <button className="text-xs text-slate-400 hover:text-slate-200" onClick={expandAll}>
              Expand all
            </button>
            <span className="text-slate-700">|</span>
            <button className="text-xs text-slate-400 hover:text-slate-200" onClick={collapseAll}>
              Collapse all
            </button>
          </div>
        )}
      </div>

      {/* Body */}
      {loading && !data ? (
        <div className="flex items-center justify-center h-64 text-slate-400">
          <Loader2 className="animate-spin mr-2" size={18} /> Loading reader stats…
        </div>
      ) : error ? (
        <div className="text-sm text-rose-400 mt-8 text-center">{error}</div>
      ) : entries.length === 0 ? (
        <div className="text-sm text-slate-500 mt-8 text-center">
          No reader activity in the last {data?.duration || duration}.
        </div>
      ) : activeGroup === 'ip' ? (
        <IpView entries={entries} expanded={expanded} toggle={toggle} />
      ) : (
        <BookView entries={entries} expanded={expanded} toggle={toggle} />
      )}
    </div>
  )
}

function IpView({ entries, expanded, toggle }) {
  return (
    <div className="space-y-2">
      {entries.map(entry => {
        const isExpanded = expanded.has(entry.ip)
        const geoText = [entry.city, entry.region, entry.country].filter(Boolean).join(', ')
        return (
          <div key={entry.ip} className="border border-slate-700 rounded-lg overflow-hidden">
            <button
              className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-slate-800/50 transition-colors"
              onClick={() => toggle(entry.ip)}
            >
              {isExpanded
                ? <ChevronDown size={14} className="text-slate-400 shrink-0" />
                : <ChevronRight size={14} className="text-slate-400 shrink-0" />}
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-sm font-mono font-medium text-slate-200">{entry.ip}</span>
                  {entry.hostname && (
                    <span className="text-xs px-1.5 py-0.5 rounded bg-slate-700 text-slate-300 flex items-center gap-1">
                      <Server size={10} /> {entry.hostname}
                    </span>
                  )}
                  {geoText && (
                    <span className="text-xs px-1.5 py-0.5 rounded bg-slate-700 text-slate-300 flex items-center gap-1">
                      <Globe size={10} /> {geoText}
                    </span>
                  )}
                  {entry.org && (
                    <span className="text-xs px-1.5 py-0.5 rounded bg-slate-800 text-slate-400">
                      {entry.org}
                    </span>
                  )}
                  <span className="ml-auto text-xs px-1.5 py-0.5 rounded bg-indigo-900/40 text-indigo-300 flex items-center gap-1">
                    <Eye size={10} /> {entry.view_count}
                  </span>
                </div>
              </div>
            </button>

            {isExpanded && (
              <div className="border-t border-slate-700 divide-y divide-slate-800">
                {entry.books.map(book => (
                  <div key={book.book_id} className="px-4 py-2.5 flex items-baseline gap-3 flex-wrap">
                    <span className="text-sm text-slate-200 font-medium">{book.title}</span>
                    {book.chapter_views > 0 && (
                      <span className="text-xs text-slate-400">
                        ch <span className="font-mono text-slate-300">{book.chapter_ranges}</span>
                        <span className="text-slate-500"> ({book.chapter_views} view{book.chapter_views !== 1 ? 's' : ''})</span>
                      </span>
                    )}
                    {book.epub_downloads > 0 && (
                      <span className="text-xs px-1.5 py-0.5 rounded bg-emerald-900/40 text-emerald-300 flex items-center gap-1">
                        <Download size={10} /> EPUB &times;{book.epub_downloads}
                      </span>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

function BookView({ entries, expanded, toggle }) {
  return (
    <div className="space-y-2">
      {entries.map(entry => {
        const key = `book-${entry.book_id}`
        const isExpanded = expanded.has(key)
        return (
          <div key={key} className="border border-slate-700 rounded-lg overflow-hidden">
            <button
              className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-slate-800/50 transition-colors"
              onClick={() => toggle(key)}
            >
              {isExpanded
                ? <ChevronDown size={14} className="text-slate-400 shrink-0" />
                : <ChevronRight size={14} className="text-slate-400 shrink-0" />}
              <BookOpen size={14} className="text-slate-400 shrink-0" />
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-sm font-medium text-slate-200">{entry.title}</span>
                  {entry.chapter_views > 0 && (
                    <span className="text-xs text-slate-400">
                      ch <span className="font-mono text-slate-300">{entry.chapter_ranges}</span>
                    </span>
                  )}
                  {entry.epub_downloads > 0 && (
                    <span className="text-xs px-1.5 py-0.5 rounded bg-emerald-900/40 text-emerald-300 flex items-center gap-1">
                      <Download size={10} /> EPUB &times;{entry.epub_downloads}
                    </span>
                  )}
                  <span className="ml-auto flex items-center gap-2">
                    <span className="text-xs px-1.5 py-0.5 rounded bg-slate-800 text-slate-300">
                      {entry.unique_ips} IP{entry.unique_ips !== 1 ? 's' : ''}
                    </span>
                    <span className="text-xs px-1.5 py-0.5 rounded bg-indigo-900/40 text-indigo-300 flex items-center gap-1">
                      <Eye size={10} /> {entry.view_count}
                    </span>
                  </span>
                </div>
              </div>
            </button>

            {isExpanded && (
              <div className="border-t border-slate-700 divide-y divide-slate-800">
                {entry.readers.map(reader => {
                  const geoText = [reader.city, reader.region, reader.country].filter(Boolean).join(', ')
                  return (
                    <div key={reader.ip} className="px-4 py-2.5 flex items-baseline gap-3 flex-wrap">
                      <span className="text-sm font-mono text-slate-200">{reader.ip}</span>
                      {reader.hostname && (
                        <span className="text-xs px-1.5 py-0.5 rounded bg-slate-700 text-slate-300 flex items-center gap-1">
                          <Server size={10} /> {reader.hostname}
                        </span>
                      )}
                      {geoText && (
                        <span className="text-xs px-1.5 py-0.5 rounded bg-slate-700 text-slate-300 flex items-center gap-1">
                          <Globe size={10} /> {geoText}
                        </span>
                      )}
                      {reader.org && (
                        <span className="text-xs px-1.5 py-0.5 rounded bg-slate-800 text-slate-400">
                          {reader.org}
                        </span>
                      )}
                      {reader.chapter_views > 0 && (
                        <span className="text-xs text-slate-400">
                          ch <span className="font-mono text-slate-300">{reader.chapter_ranges}</span>
                          <span className="text-slate-500"> ({reader.chapter_views} view{reader.chapter_views !== 1 ? 's' : ''})</span>
                        </span>
                      )}
                      {reader.epub_downloads > 0 && (
                        <span className="text-xs px-1.5 py-0.5 rounded bg-emerald-900/40 text-emerald-300 flex items-center gap-1">
                          <Download size={10} /> EPUB &times;{reader.epub_downloads}
                        </span>
                      )}
                      <span className="ml-auto text-xs px-1.5 py-0.5 rounded bg-indigo-900/40 text-indigo-300 flex items-center gap-1">
                        <Eye size={10} /> {reader.view_count}
                      </span>
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
