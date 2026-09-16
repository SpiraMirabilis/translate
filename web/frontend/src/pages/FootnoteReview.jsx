import { useEffect, useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import {
  ArrowLeft, Asterisk, Check, X, Loader2, Pencil, RotateCcw, Search,
} from 'lucide-react'
import { api } from '../services/api'
import { useSite } from '../App'

const STATUS_STYLES = {
  pending:  'bg-blue-500/20 text-blue-400',
  accepted: 'bg-emerald-500/20 text-emerald-400',
  rejected: 'bg-slate-500/20 text-slate-500 line-through',
}

const TABS = [
  { value: null,       label: 'All' },
  { value: 'pending',  label: 'Pending' },
  { value: 'accepted', label: 'Kept' },
  { value: 'rejected', label: 'Rejected' },
  { value: 'flagged',  label: 'Flagged' },
]

// Per-book footnote-candidate review: keep / reject / edit suggestions.
// Mirrors the CLI TUI's semantics — dup = later repeat of a term first seen in
// an earlier chapter; already = term is already a real footnote in this book;
// unreviewed (pending) rows still export, rejection is the only veto.
export default function FootnoteReview() {
  const { bookId } = useParams()
  const { site_name } = useSite()
  const queryClient = useQueryClient()
  const [tab, setTab] = useState('pending')
  const [firstOnly, setFirstOnly] = useState(true)
  const [search, setSearch] = useState('')
  const [selected, setSelected] = useState(() => new Set())
  const [editing, setEditing] = useState(null)      // candidate id
  const [draft, setDraft] = useState({})            // {term_en, body} while editing
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState(null)

  const queryKey = ['footnote-candidates', bookId]
  const { data, isPending, error } = useQuery({
    queryKey,
    queryFn: () => api.listFootnoteCandidates(bookId),
  })
  const { data: bookData } = useQuery({
    queryKey: ['book', bookId],
    queryFn: () => api.getBook(bookId),
  })
  const bookTitle = bookData?.title || `Book ${bookId}`
  const rows = useMemo(() => data?.candidates || [], [data])

  useEffect(() => {
    document.title = `Footnotes — ${bookTitle} | ${site_name}`
    return () => { document.title = site_name }
  }, [bookTitle, site_name])

  // Patch rows in the cache after a mutation — no refetch of the whole list.
  const patchRows = (updater) => {
    queryClient.setQueryData(queryKey, (old) =>
      old ? { ...old, candidates: old.candidates.map(updater) } : old)
  }

  const setStatus = async (ids, status) => {
    setActionError(null)
    setBusy(true)
    try {
      if (ids.length === 1) {
        await api.updateFootnoteCandidate(ids[0], { status })
      } else {
        await api.batchFootnoteCandidates(bookId, ids, status)
      }
      const idSet = new Set(ids)
      patchRows(r => (idSet.has(r.id) ? { ...r, status } : r))
      setSelected(prev => {
        const next = new Set(prev)
        ids.forEach(i => next.delete(i))
        return next
      })
    } catch (e) {
      setActionError(`Failed to update: ${e.message}`)
    } finally {
      setBusy(false)
    }
  }

  const saveEdit = async (id) => {
    setActionError(null)
    setBusy(true)
    try {
      const { candidate } = await api.updateFootnoteCandidate(id, {
        term_en: draft.term_en, body: draft.body,
      })
      patchRows(r => (r.id === id ? { ...r, ...candidate } : r))
      setEditing(null)
    } catch (e) {
      setActionError(`Failed to save: ${e.message}`)
    } finally {
      setBusy(false)
    }
  }

  const visible = useMemo(() => {
    const q = search.trim().toLowerCase()
    return rows.filter(r => {
      if (tab === 'flagged' && !(r.dup || r.already)) return false
      if (tab && tab !== 'flagged' && r.status !== tab) return false
      if (firstOnly && r.dup) return false
      if (q) {
        const hay = [r.term_zh, r.term_en, r.body, r.sentence]
          .map(v => String(v || '').toLowerCase()).join(' ')
        if (!hay.includes(q)) return false
      }
      return true
    })
  }, [rows, tab, firstOnly, search])

  const byChapter = useMemo(() => {
    const groups = new Map()
    for (const r of visible) {
      if (!groups.has(r.chapter_number)) groups.set(r.chapter_number, [])
      groups.get(r.chapter_number).push(r)
    }
    return [...groups.entries()]
  }, [visible])

  const counts = useMemo(() => {
    const c = { pending: 0, accepted: 0, rejected: 0 }
    rows.forEach(r => { c[r.status] = (c[r.status] || 0) + 1 })
    return c
  }, [rows])

  const toggleSelect = (id) => setSelected(prev => {
    const next = new Set(prev)
    next.has(id) ? next.delete(id) : next.add(id)
    return next
  })
  const allVisibleSelected = visible.length > 0 && visible.every(r => selected.has(r.id))
  const toggleSelectAll = () => setSelected(
    allVisibleSelected ? new Set() : new Set(visible.map(r => r.id)))

  const startEdit = (r) => {
    setEditing(r.id)
    setDraft({ term_en: r.term_en || '', body: r.body || '' })
  }

  return (
    <div className="max-w-5xl mx-auto">
      <div className="flex items-center gap-2 mb-1">
        <Link to="/footnotes" className="btn-ghost p-1.5" title="All books"><ArrowLeft size={16} /></Link>
        <Asterisk size={20} className="text-indigo-400" />
        <h1 className="text-xl font-semibold truncate">{bookTitle}</h1>
      </div>
      <div className="text-sm text-slate-400 mb-4 ml-9">
        {rows.length} candidate{rows.length === 1 ? '' : 's'} · {counts.pending} pending ·{' '}
        {counts.accepted} kept · {counts.rejected} rejected
        {data?.scan?.chapters_scanned > 0 && <> · {data.scan.chapters_scanned} chapters scanned</>}
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-2 mb-4">
        <div className="flex gap-1 bg-slate-800 border border-slate-700 rounded-lg p-1">
          {TABS.map(t => (
            <button
              key={t.label}
              className={`px-3 py-1 rounded text-xs ${((tab ?? null) === t.value) ? 'bg-indigo-600 text-white' : 'text-slate-300 hover:bg-slate-700'}`}
              onClick={() => setTab(t.value)}
            >
              {t.label}
            </button>
          ))}
        </div>
        <label className="flex items-center gap-1.5 text-xs text-slate-300 select-none">
          <input type="checkbox" checked={firstOnly} onChange={e => setFirstOnly(e.target.checked)} />
          First mentions only
        </label>
        <div className="relative ml-auto">
          <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500" />
          <input
            className="input pl-8 py-1.5 text-xs w-56"
            placeholder="Search candidates..."
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
        </div>
      </div>

      {actionError && <div className="text-rose-400 text-sm mb-3">{actionError}</div>}

      {/* Batch bar */}
      {selected.size > 0 && (
        <div className="sticky top-0 z-10 flex items-center gap-3 bg-slate-800 border border-indigo-500/50 rounded-lg px-4 py-2 mb-3 text-sm">
          <span>{selected.size} selected</span>
          <button className="btn-ghost text-emerald-400 text-xs flex items-center gap-1" disabled={busy}
                  onClick={() => setStatus([...selected], 'accepted')}>
            <Check size={13} /> Keep
          </button>
          <button className="btn-ghost text-rose-400 text-xs flex items-center gap-1" disabled={busy}
                  onClick={() => setStatus([...selected], 'rejected')}>
            <X size={13} /> Reject
          </button>
          <button className="btn-ghost text-slate-400 text-xs flex items-center gap-1" disabled={busy}
                  onClick={() => setStatus([...selected], 'pending')}>
            <RotateCcw size={13} /> Unmark
          </button>
          <button className="btn-ghost text-slate-400 text-xs ml-auto" onClick={() => setSelected(new Set())}>
            Clear
          </button>
        </div>
      )}

      {isPending && (
        <div className="flex justify-center py-16"><Loader2 size={24} className="animate-spin text-indigo-400" /></div>
      )}
      {error && <div className="text-rose-400 text-sm">Failed to load: {error.message}</div>}
      {!isPending && !error && visible.length === 0 && (
        <div className="text-slate-400 text-sm border border-slate-700 rounded-lg p-6 text-center">
          No candidates match the current filter.
        </div>
      )}

      {visible.length > 0 && (
        <label className="flex items-center gap-1.5 text-xs text-slate-400 select-none mb-2 ml-1">
          <input type="checkbox" checked={allVisibleSelected} onChange={toggleSelectAll} />
          Select all {visible.length} shown
        </label>
      )}

      {byChapter.map(([chapterNumber, chRows]) => (
        <div key={chapterNumber} className="mb-5">
          <div className="text-xs font-medium text-slate-400 uppercase tracking-wider mb-1.5">
            Chapter {chapterNumber}
            {chRows[0].chapter_title && <span className="normal-case tracking-normal"> — {chRows[0].chapter_title}</span>}
          </div>
          <div className="flex flex-col gap-1.5">
            {chRows.map(r => (
              <div key={r.id}
                   className={`bg-slate-800 border rounded-lg px-3 py-2 ${r.status === 'rejected' ? 'border-slate-700/50 opacity-60' : 'border-slate-700'}`}>
                <div className="flex items-start gap-2">
                  <input type="checkbox" className="mt-1" checked={selected.has(r.id)}
                         onChange={() => toggleSelect(r.id)} />
                  <div className="flex-1 min-w-0">
                    <div className="flex flex-wrap items-center gap-2 text-sm">
                      {r.term_zh && <span className="font-medium">{r.term_zh}</span>}
                      {r.term_zh && (r.term_en || editing === r.id) && <span className="text-slate-500">→</span>}
                      {editing === r.id ? (
                        <input
                          className="input py-0.5 px-1.5 text-sm w-64"
                          value={draft.term_en}
                          onChange={e => setDraft(d => ({ ...d, term_en: e.target.value }))}
                          placeholder="English term (as published)"
                        />
                      ) : (
                        r.term_en && <span className="text-indigo-300">{r.term_en}</span>
                      )}
                      <span className={`px-1.5 py-0.5 rounded-full text-[10px] ${STATUS_STYLES[r.status] || ''}`}>
                        {r.status === 'accepted' ? 'kept' : r.status}
                      </span>
                      {r.dup && (
                        <span className="px-1.5 py-0.5 rounded-full text-[10px] bg-amber-500/20 text-amber-400"
                              title="Repeat — this term was first collected in an earlier chapter">dup</span>
                      )}
                      {r.already && (
                        <span className="px-1.5 py-0.5 rounded-full text-[10px] bg-purple-500/20 text-purple-400"
                              title="This term is already a real footnote in the book">already footnoted</span>
                      )}
                    </div>
                    {editing === r.id ? (
                      <div className="mt-1.5">
                        <textarea
                          className="input w-full text-sm py-1.5"
                          rows={3}
                          value={draft.body}
                          onChange={e => setDraft(d => ({ ...d, body: e.target.value }))}
                        />
                        <div className="flex gap-2 mt-1">
                          <button className="btn-primary text-xs px-3 py-1" disabled={busy || !draft.body.trim()}
                                  onClick={() => saveEdit(r.id)}>
                            {busy ? <Loader2 size={12} className="animate-spin" /> : 'Save'}
                          </button>
                          <button className="btn-ghost text-xs px-3 py-1" onClick={() => setEditing(null)}>Cancel</button>
                        </div>
                      </div>
                    ) : (
                      <>
                        <div className="text-sm text-slate-300 mt-1">{r.body}</div>
                        {r.sentence && (
                          <div className="text-xs text-slate-500 mt-1 truncate" title={r.sentence}>{r.sentence}</div>
                        )}
                      </>
                    )}
                  </div>
                  {editing !== r.id && (
                    <div className="flex items-center gap-0.5 shrink-0">
                      <button
                        className={`btn-ghost p-1.5 ${r.status === 'accepted' ? 'text-emerald-400' : 'text-slate-400 hover:text-emerald-400'}`}
                        title="Keep" disabled={busy}
                        onClick={() => setStatus([r.id], r.status === 'accepted' ? 'pending' : 'accepted')}
                      >
                        <Check size={15} />
                      </button>
                      <button
                        className={`btn-ghost p-1.5 ${r.status === 'rejected' ? 'text-rose-400' : 'text-slate-400 hover:text-rose-400'}`}
                        title="Reject" disabled={busy}
                        onClick={() => setStatus([r.id], r.status === 'rejected' ? 'pending' : 'rejected')}
                      >
                        <X size={15} />
                      </button>
                      <button className="btn-ghost p-1.5 text-slate-400 hover:text-indigo-300" title="Edit term / body"
                              onClick={() => startEdit(r)}>
                        <Pencil size={13} />
                      </button>
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}
