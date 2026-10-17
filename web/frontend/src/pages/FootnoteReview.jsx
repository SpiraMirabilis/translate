import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import {
  Anchor, ArrowLeft, Asterisk, Check, X, Loader2, Pencil, RotateCcw, Search,
} from 'lucide-react'
import { api } from '../services/api'
import { useSite } from '../App'

const STATUS_STYLES = {
  applied:  'bg-indigo-500/20 text-indigo-300',
  pending:  'bg-blue-500/20 text-blue-400',
  accepted: 'bg-emerald-500/20 text-emerald-400',
  rejected: 'bg-slate-500/20 text-slate-500 line-through',
}

const STATUS_LABELS = { accepted: 'kept', applied: 'placed · unreviewed' }

const TABS = [
  { value: 'applied',  label: 'Applied' },
  { value: 'pending',  label: 'Pending' },
  { value: 'accepted', label: 'Kept' },
  { value: 'rejected', label: 'Rejected' },
  { value: 'flagged',  label: 'Flagged' },
  { value: null,       label: 'All' },
]

// A row owns a footnote in the chapter (auto-applied by the translation pass,
// or placed from here). "missing" = the footnote was deleted by hand since.
const isPlaced = (r) => !!r.footnote_id && r.footnote_status !== 'missing'

// What a review decision does to a row's local state — mirrors
// footnote_scan_core.decide_candidates: rejecting a placed row removes its
// footnote; "unmarking" one returns it to applied, never to plain pending.
const applyDecision = (r, status) => {
  if (!r.footnote_id) return { ...r, status }
  if (r.footnote_status === 'missing' || status === 'rejected') {
    return { ...r, status, footnote_id: null, footnote_status: null }
  }
  return { ...r, status: status === 'pending' ? 'applied' : status }
}

// Per-book footnote-candidate review: keep / reject / edit suggestions.
// Mirrors the CLI TUI's semantics — dup = later repeat of a term first seen in
// an earlier chapter; already = term is already a real footnote in this book;
// unreviewed (pending) rows still export, rejection is the only veto.
// "Applied" rows were placed into the chapter by the translation pass: keep
// leaves the footnote, reject removes it, editing rewrites/re-anchors it.
export default function FootnoteReview() {
  const { bookId } = useParams()
  const { site_name } = useSite()
  const queryClient = useQueryClient()
  const [tab, setTab] = useState('pending')
  const tabTouched = useRef(false)
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

  // Open on Applied when the translator has placed anything awaiting review.
  useEffect(() => {
    if (!tabTouched.current && rows.some(r => r.status === 'applied')) setTab('applied')
  }, [rows])
  const chooseTab = (value) => { tabTouched.current = true; setTab(value) }

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
    if (status === 'rejected') {
      const placed = rows.filter(r => ids.includes(r.id) && isPlaced(r) && r.status !== 'rejected')
      if (placed.length && !window.confirm(placed.length === 1
        ? `Reject this footnote? It will be removed from chapter ${placed[0].chapter_number}.`
        : `Reject ${placed.length} placed footnotes? They will be removed from their chapters.`)) return
    }
    setActionError(null)
    setBusy(true)
    try {
      if (ids.length === 1) {
        await api.updateFootnoteCandidate(ids[0], { status })
      } else {
        await api.batchFootnoteCandidates(bookId, ids, status)
      }
      const idSet = new Set(ids)
      patchRows(r => (idSet.has(r.id) ? applyDecision(r, status) : r))
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

  const place = async (id) => {
    setActionError(null)
    setBusy(true)
    try {
      await api.placeFootnoteCandidate(id)
      // Placing changes other rows' "already footnoted" flags too — refetch.
      await queryClient.invalidateQueries({ queryKey })
    } catch (e) {
      setActionError(`Failed to place: ${e.message}`)
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
    const c = { applied: 0, pending: 0, accepted: 0, rejected: 0 }
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
        {rows.length} candidate{rows.length === 1 ? '' : 's'} ·{' '}
        {counts.applied > 0 && <>{counts.applied} placed awaiting review · </>}
        {counts.pending} pending ·{' '}
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
              onClick={() => chooseTab(t.value)}
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
                        {STATUS_LABELS[r.status] || r.status}
                      </span>
                      {isPlaced(r) && (
                        <Link
                          to={`/books/${bookId}/chapters/${r.chapter_number}/edit?search=${encodeURIComponent(r.term_en || '')}&searchScope=translated`}
                          className={`px-1.5 py-0.5 rounded-full text-[10px] ${r.footnote_status === 'orphaned' ? 'bg-rose-500/20 text-rose-400' : 'bg-indigo-500/20 text-indigo-300'} hover:underline`}
                          title={r.footnote_status === 'orphaned'
                            ? 'Placed, but its anchor is no longer in the chapter (orphaned) — edit the term to re-anchor it'
                            : 'Footnote is in the chapter — open it in the editor'}
                        >
                          {r.footnote_status === 'orphaned' ? 'orphaned' : 'in chapter'}
                        </Link>
                      )}
                      {r.footnote_status === 'missing' && (
                        <span className="px-1.5 py-0.5 rounded-full text-[10px] bg-rose-500/20 text-rose-400"
                              title="Was placed, but the footnote has since been deleted">footnote deleted</span>
                      )}
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
                      {!isPlaced(r) && r.status !== 'rejected' && (
                        <button className="btn-ghost p-1.5 text-slate-400 hover:text-indigo-300"
                                title="Place as a footnote (anchored on the English term, first match below the heading)"
                                disabled={busy || !r.term_en} onClick={() => place(r.id)}>
                          <Anchor size={13} />
                        </button>
                      )}
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
