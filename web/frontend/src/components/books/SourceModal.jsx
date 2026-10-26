import { useState, useEffect, useMemo } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  X, Loader2, RefreshCw, Link2, Download, AlertTriangle, ExternalLink, Ban, RotateCcw, Search,
} from 'lucide-react'
import { api } from '../../services/api'
import { relativeTime } from '../../lib/relativeTime'
import { MODES, TASK_STATUS_CHIP, taskLabel, taskSummary, OPEN_TASK_STATES } from '../../lib/scrape'

/**
 * Per-book automated scraping: mode, the cursor into the source TOC (link),
 * backfills, and the book's recent scrape tasks. The work runs in
 * t9-scraper.service; everything here only queues it, so the modal polls
 * while a task for this book is open.
 */
export default function SourceModal({ bookId, onClose }) {
  const queryClient = useQueryClient()
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(null)
  const [selected, setSelected] = useState(null)   // TOC index picked in the list
  const [linkNumber, setLinkNumber] = useState('')
  const [rangeTo, setRangeTo] = useState('')
  const [filter, setFilter] = useState('')
  const [form, setForm] = useState(null)

  const { data, isPending } = useQuery({
    queryKey: ['book-scrape', bookId],
    queryFn: () => api.getBookScrape(bookId),
    refetchInterval: (q) =>
      (q.state.data?.tasks || []).some(t => OPEN_TASK_STATES.includes(t.status)) ? 4000 : false,
  })

  const state = data?.state
  const toc = useMemo(() => data?.toc || [], [data])

  useEffect(() => {
    if (!state || form) return
    setForm({
      mode: state.mode || 'off',
      auto_gap: state.auto_gap ?? '',
      number_offset: state.number_offset ?? 0,
      no_review: state.run_options?.no_review ?? true,
      save_as_draft: state.run_options?.save_as_draft ?? false,
    })
  }, [state, form])

  // The mapping TOC index -> our chapter number. A title-matched proposal is
  // the strongest evidence (it is how book 130's off-by-3 link was caught);
  // then the current cursor; then the proposal's weaker methods.
  const anchor = useMemo(() => {
    const p = data?.proposal
    const st = data?.state
    if (p?.method === 'title' && p.index >= 0) return { index: p.index, number: p.chapter_number }
    if (st?.linked && data?.cursor_index >= 0 && st.cursor_number != null)
      return { index: data.cursor_index, number: st.cursor_number }
    if (p && p.index >= 0) return { index: p.index, number: p.chapter_number }
    return null
  }, [data])

  // Default the picker to the anchor once a TOC exists.
  useEffect(() => {
    if (selected !== null || !toc.length || !anchor) return
    setSelected(anchor.index)
  }, [anchor, selected, toc.length])

  useEffect(() => {
    if (selected == null || !toc[selected]) return
    setLinkNumber(String(anchor ? anchor.number + (selected - anchor.index) : selected + 1))
  }, [selected])  // eslint-disable-line react-hooks/exhaustive-deps

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['book-scrape', bookId] })
    queryClient.invalidateQueries({ queryKey: ['scrape-overview'] })
  }

  const run = async (label, fn) => {
    setBusy(label); setError(null)
    try {
      await fn()
      refresh()
    } catch (e) {
      setError(e.detail || e.message)
    } finally {
      setBusy(null)
    }
  }

  const save = () => run('save', () => api.updateBookScrape(bookId, {
    mode: form.mode,
    number_offset: parseInt(form.number_offset, 10) || 0,
    ...(form.auto_gap === '' ? { clear_auto_gap: true } : { auto_gap: parseInt(form.auto_gap, 10) }),
    run_options: { no_review: form.no_review, save_as_draft: form.save_as_draft },
  }))

  const filtered = useMemo(() => {
    const q = filter.trim()
    if (!q) return toc
    return toc.filter(e => e.title?.includes(q) || String(e.index + 1) === q || String(e.number) === q)
  }, [toc, filter])

  const cursorIdx = data?.cursor_index
  const linked = !!state?.linked
  const pending = state?.pending_count

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="card w-full max-w-3xl max-w-[95vw] max-h-[92vh] flex flex-col shadow-2xl">
        <div className="flex items-center justify-between px-5 py-3 border-b border-slate-700 shrink-0">
          <div className="min-w-0">
            <h2 className="font-semibold text-slate-200">Source scraping</h2>
            {data?.source_url ? (
              <a href={data.source_url} target="_blank" rel="noreferrer"
                 className="text-xs text-indigo-400 hover:text-indigo-300 inline-flex items-center gap-1 truncate max-w-full">
                {data.source_url} <ExternalLink size={11} />
              </a>
            ) : (
              <p className="text-xs text-amber-400 mt-0.5">No source URL — set one in Edit Book first.</p>
            )}
          </div>
          <button className="btn-ghost p-1" onClick={onClose}><X size={16} /></button>
        </div>

        {isPending || !form ? (
          <div className="flex justify-center py-16"><Loader2 size={24} className="animate-spin text-indigo-400" /></div>
        ) : (
          <div className="overflow-y-auto px-5 py-4 space-y-5">
            {error && (
              <div className="px-3 py-2 rounded-lg bg-rose-500/10 border border-rose-700/50 text-sm text-rose-300">{error}</div>
            )}

            {data.site_supported === false && (
              <div className="px-3 py-2 rounded-lg bg-slate-800 border border-slate-700 text-sm text-slate-300">
                The scraper doesn&apos;t service <span className="font-mono">{data.site}</span> yet. The daily check
                skips this book; once the site is supported, its TOC is read automatically.
              </div>
            )}

            {state.hold_reason && (
              <div className="px-3 py-2 rounded-lg bg-amber-500/10 border border-amber-700/50 text-sm text-amber-200 flex gap-3 items-start">
                <AlertTriangle size={15} className="mt-0.5 shrink-0" />
                <div className="flex-1 min-w-0">
                  <div className="font-medium">Held — auto-translate won&apos;t start until you clear this</div>
                  <div className="text-xs text-amber-300/80 mt-1 break-words">{state.hold_reason}</div>
                </div>
                <button className="btn btn-secondary text-xs shrink-0" disabled={!!busy}
                        onClick={() => run('hold', () => api.updateBookScrape(bookId, { clear_hold: true }))}>
                  Clear hold
                </button>
              </div>
            )}

            {/* Status line */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm">
              <Stat label="Source TOC" value={state.toc_count ?? '—'}
                    sub={state.last_checked_at ? `checked ${relativeTime(state.last_checked_at)}` : 'never checked'} />
              <Stat label="We hold up to" value={data.have_max != null ? `ch ${data.have_max}` : '—'}
                    sub={`${data.queued_count} queued`} />
              <Stat label="Translated to" value={data.translated_max != null ? `ch ${data.translated_max}` : '—'} />
              <Stat label="New at source" value={linked ? (pending ?? '—') : 'not linked'}
                    sub={state.toc_latest_title || ''} />
            </div>

            {/* Mode + options */}
            <div className="card p-4 space-y-3">
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                <div className="sm:col-span-3">
                  <label className="label">Mode</label>
                  <div className="flex gap-1 flex-wrap">
                    {MODES.map(m => (
                      <button key={m.value} type="button" onClick={() => setForm(f => ({ ...f, mode: m.value }))}
                              className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
                                form.mode === m.value ? 'bg-indigo-600 text-white'
                                  : 'bg-slate-800 text-slate-400 hover:text-slate-200 hover:bg-slate-700'}`}>
                        {m.label}
                      </button>
                    ))}
                  </div>
                  <p className="text-xs text-slate-500 mt-1">{MODES.find(m => m.value === form.mode)?.hint}</p>
                </div>
                <div>
                  <label className="label">Auto-translate gap</label>
                  <input className="input text-sm w-full" type="number" min="0" value={form.auto_gap}
                         placeholder={`default ${data.default_gap}`}
                         onChange={e => setForm(f => ({ ...f, auto_gap: e.target.value }))} />
                </div>
                <div>
                  <label className="label">Number offset</label>
                  <input className="input text-sm w-full" type="number" value={form.number_offset}
                         onChange={e => setForm(f => ({ ...f, number_offset: e.target.value }))} />
                </div>
                <div className="flex flex-col justify-end gap-1 text-sm text-slate-300">
                  <label className="flex items-center gap-2">
                    <input type="checkbox" checked={form.no_review}
                           onChange={e => setForm(f => ({ ...f, no_review: e.target.checked }))} />
                    Skip entity review
                  </label>
                  <label className="flex items-center gap-2">
                    <input type="checkbox" checked={form.save_as_draft}
                           onChange={e => setForm(f => ({ ...f, save_as_draft: e.target.checked }))} />
                    Save as draft
                  </label>
                </div>
              </div>
              <div className="flex items-center gap-3 flex-wrap">
                <p className="text-xs text-slate-500 flex-1">
                  Auto-translate uses the model of the latest translated chapter
                  {data.default_model ? <> (<span className="font-mono text-slate-400">{data.default_model}</span>)</> : ''}.
                  The gap is how far the newest queued chapter may run past the newest translated one.
                </p>
                <button className="btn btn-primary text-xs" disabled={!!busy} onClick={save}>
                  {busy === 'save' ? <Loader2 size={12} className="animate-spin" /> : 'Save'}
                </button>
              </div>
            </div>

            {/* TOC actions */}
            <div className="flex gap-2 flex-wrap items-center">
              <button className="btn btn-secondary text-xs inline-flex items-center gap-1.5" disabled={!!busy || !data.source_url}
                      title="What the daily check does, now: read the TOC, and when the mode is on, scrape what's new"
                      onClick={() => run('check', () => api.scrapeCheckNow(bookId))}>
                <RefreshCw size={12} /> Check now
              </button>
              <button className="btn btn-secondary text-xs inline-flex items-center gap-1.5"
                      disabled={!!busy || !linked || !data.source_url}
                      title={linked ? 'Read the TOC and scrape everything after the cursor, whatever the mode' : 'Link the book first'}
                      onClick={() => run('catchup', () => api.scrapeCheck(bookId, true))}>
                <Download size={12} /> Scrape new now
              </button>
              {form.mode === 'off' && (
                <span className="text-xs text-slate-500">Mode is off: “Check now” only refreshes the TOC.</span>
              )}
            </div>

            {/* TOC browser: link + backfill */}
            {toc.length > 0 && (
              <div className="card p-4 space-y-3">
                <LinkStatus toc={toc} linked={linked} cursorIdx={cursorIdx} state={state}
                            proposal={data.proposal} haveMax={data.have_max} busy={busy}
                            onLink={(p) => run('link', () => api.scrapeLink(bookId, p.index, p.chapter_number))} />

                <div className="relative">
                  <Search size={13} className="absolute left-2.5 top-2.5 text-slate-500" />
                  <input className="input text-sm w-full pl-8" placeholder="Filter by title, #position or chapter number"
                         value={filter} onChange={e => setFilter(e.target.value)} />
                </div>
                <div className="max-h-56 overflow-y-auto border border-slate-700 rounded">
                  {filtered.map(e => (
                    <button key={e.index} onClick={() => setSelected(e.index)}
                            className={`w-full text-left px-3 py-1 text-xs flex gap-3 items-baseline border-b border-slate-800 last:border-0 ${
                              selected === e.index ? 'bg-indigo-600/30 text-slate-100'
                                : e.index === cursorIdx ? 'bg-emerald-500/10 text-slate-200'
                                  : cursorIdx != null && e.index > cursorIdx ? 'text-sky-300 hover:bg-slate-800'
                                    : 'text-slate-400 hover:bg-slate-800'}`}>
                      <span className="font-mono w-12 shrink-0 text-slate-500">#{e.index + 1}</span>
                      <span className="flex-1 truncate">{e.title}</span>
                      {e.index === cursorIdx && <span className="text-emerald-400 shrink-0">cursor</span>}
                    </button>
                  ))}
                </div>

                {selected != null && toc[selected] && (
                  <div className="flex flex-wrap items-end gap-3 text-sm">
                    <div>
                      <label className="label">#{selected + 1} is our chapter</label>
                      <input className="input text-sm w-24" type="number" value={linkNumber}
                             onChange={e => setLinkNumber(e.target.value)} />
                    </div>
                    <button className="btn btn-primary text-xs inline-flex items-center gap-1.5" disabled={!!busy}
                            onClick={() => run('link', () => api.scrapeLink(bookId, selected, parseInt(linkNumber, 10) || null))}>
                      <Link2 size={12} /> Link here
                    </button>
                    <div className="w-px h-8 bg-slate-700 mx-1" />
                    <div>
                      <label className="label">Backfill to #</label>
                      <input className="input text-sm w-24" type="number" min={selected + 1} max={toc.length}
                             placeholder={String(toc.length)} value={rangeTo}
                             onChange={e => setRangeTo(e.target.value)} />
                    </div>
                    <button className="btn btn-secondary text-xs inline-flex items-center gap-1.5" disabled={!!busy}
                            title="Scrape this range into the queue; chapter numbers we already hold are skipped"
                            onClick={() => run('backfill', () => api.scrapeBackfill(
                              bookId, selected, rangeTo ? parseInt(rangeTo, 10) - 1 : null))}>
                      <Download size={12} /> Backfill from #{selected + 1}
                    </button>
                  </div>
                )}
                {!linked && (
                  <button className="btn-ghost text-xs text-slate-400" disabled={!!busy}
                          onClick={() => run('link', () => api.scrapeLink(bookId, -1))}>
                    Link before the first entry (everything is new)
                  </button>
                )}
              </div>
            )}

            {/* Recent tasks */}
            {data.tasks?.length > 0 && (
              <div>
                <div className="text-xs text-slate-500 mb-1.5">Recent tasks</div>
                <div className="space-y-1">
                  {data.tasks.map(t => <TaskRow key={t.id} task={t} onChanged={refresh} setError={setError} />)}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

const METHOD_LABEL = {
  title: 'matched by chapter title',
  number: 'matched by chapter number',
  position: 'by TOC position — check it',
}

function LinkStatus({ toc, linked, cursorIdx, state, proposal: p, haveMax, busy, onLink }) {
  const proposalDiffers = p && p.index >= 0 && linked &&
    (p.index !== cursorIdx || p.chapter_number !== state.cursor_number)
  const describe = (idx, num) => <><b>#{idx + 1}</b> “{toc[idx]?.title}” = our <b>ch {num ?? '?'}</b></>
  return (
    <div className="text-sm text-slate-300 space-y-2">
      <div>
        {linked ? (
          cursorIdx == null
            ? <span className="text-amber-300">The linked chapter is no longer in the TOC — relink below.</span>
            : cursorIdx === -1
              ? <>Linked <b>before the first entry</b> — everything in the TOC counts as new.</>
              : <>Linked at {describe(cursorIdx, state.cursor_number)}
                  {state.number_offset ? <span className="text-slate-500"> (title number {state.number_offset > 0 ? '+' : ''}{state.number_offset})</span> : null}.</>
        ) : (
          <span className="text-amber-300">Not linked.</span>
        )}
      </div>
      {p && p.index >= 0 && (!linked || proposalDiffers) && (
        <div className={`flex items-center gap-3 flex-wrap px-3 py-2 rounded ${proposalDiffers ? 'bg-amber-500/10 border border-amber-700/50' : 'bg-slate-800/60'}`}>
          <span className="flex-1 min-w-0">
            {proposalDiffers ? 'Suggested instead: ' : 'Suggested: '}
            {describe(p.index, p.chapter_number)}
            <span className="text-slate-500"> — {METHOD_LABEL[p.method] || p.method}
              {p.method === 'title' && p.matched > 1 ? ` (${p.matched} titles agree)` : ''}</span>
          </span>
          <button className="btn btn-primary text-xs inline-flex items-center gap-1.5" disabled={!!busy}
                  onClick={() => onLink(p)}>
            <Link2 size={12} /> {proposalDiffers ? 'Relink as suggested' : 'Link as suggested'}
          </button>
        </div>
      )}
      {!p && haveMax != null && !linked && (
        <div className="text-slate-500">No TOC entry lines up with ch {haveMax} — pick one below.</div>
      )}
    </div>
  )
}

function Stat({ label, value, sub }) {
  return (
    <div className="bg-slate-800/60 rounded-lg px-3 py-2 min-w-0">
      <div className="text-[11px] text-slate-500">{label}</div>
      <div className="text-slate-100 font-semibold">{value}</div>
      {sub && <div className="text-[11px] text-slate-500 truncate" title={sub}>{sub}</div>}
    </div>
  )
}

export function TaskRow({ task: t, onChanged, setError, showBook = false, onOpenBook }) {
  const act = async (fn) => {
    try { await fn(); onChanged() } catch (e) { setError?.(e.detail || e.message) }
  }
  const progress = t.progress || {}
  return (
    <div className="flex items-center gap-2 text-xs px-3 py-1.5 rounded bg-slate-800/50 min-w-0">
      <span className={`px-1.5 py-0.5 rounded font-medium shrink-0 ${TASK_STATUS_CHIP[t.status] || ''}`}>{t.status}</span>
      {showBook && (
        <button className="text-slate-200 hover:text-indigo-300 truncate max-w-[14rem] shrink-0"
                onClick={() => onOpenBook?.(t.book_id)}>
          {t.book_title || `Book ${t.book_id}`}
        </button>
      )}
      <span className="text-slate-300 shrink-0">{taskLabel(t)}</span>
      <span className="text-slate-500 truncate flex-1" title={taskSummary(t)}>
        {t.status === 'running' || t.status === 'blocked'
          ? `${progress.chapters ?? 0} ch${progress.lastTitle ? ` · ${progress.lastTitle}` : ''}`
          : taskSummary(t)}
        {t.status === 'queued' && t.not_before && ` · after ${t.not_before.replace('T', ' ').slice(5, 16)}`}
      </span>
      {t.status === 'blocked' && t.vnc_url && (
        <a href={t.vnc_url} target="_blank" rel="noreferrer" className="text-amber-300 hover:text-amber-200 shrink-0 inline-flex items-center gap-1">
          Solve challenge <ExternalLink size={11} />
        </a>
      )}
      <span className="text-slate-600 shrink-0">{relativeTime(t.finished_at || t.started_at || t.created_at)}</span>
      {['queued', 'running', 'blocked'].includes(t.status) && (
        <button className="text-slate-500 hover:text-rose-300 shrink-0" title="Cancel"
                onClick={() => act(() => api.cancelScrapeTask(t.id))}><Ban size={12} /></button>
      )}
      {(['failed', 'cancelled'].includes(t.status) || (t.status === 'queued' && t.not_before)) && (
        <button className="text-slate-500 hover:text-indigo-300 shrink-0"
                title={t.status === 'queued' ? 'Run now' : 'Retry'}
                onClick={() => act(() => api.retryScrapeTask(t.id))}><RotateCcw size={12} /></button>
      )}
    </div>
  )
}
