import { useState, useEffect } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Newspaper, Loader2, ExternalLink, RefreshCw, Search, AlertTriangle } from 'lucide-react'
import { api } from '../services/api'
import { useSite } from '../App'
import { relativeTime } from '../lib/relativeTime'
import { STATE_LABEL, STATE_CHIP, HOLD_LABEL } from '../lib/novelupdates'
import NovelUpdatesModal, { NuTaskRow } from '../components/books/NovelUpdatesModal'

/**
 * NovelUpdates posting at a glance: what needs a human, the NU job on the
 * scraper and what's queued behind it, and every translated book's NU state.
 * The lane runs in t9-scraper.service alongside the scrape lane.
 */
export default function NovelUpdates() {
  const { site_name } = useSite()
  const queryClient = useQueryClient()
  const [openBook, setOpenBook] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(null)
  const [showAll, setShowAll] = useState(false)

  const { data, isPending } = useQuery({
    queryKey: ['nu-overview'],
    queryFn: () => api.nuOverview(),
    refetchInterval: 5000,
  })

  useEffect(() => {
    document.title = `NovelUpdates | ${site_name}`
    return () => { document.title = site_name }
  }, [site_name])

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['nu-overview'] })
  const run = async (label, fn) => {
    setBusy(label); setError(null)
    try { const r = await fn(); refresh(); return r } catch (e) { setError(e.detail || e.message) } finally { setBusy(null) }
  }

  if (isPending) {
    return <div className="flex justify-center py-16"><Loader2 size={28} className="animate-spin text-indigo-400" /></div>
  }

  const { settings = {}, worker, hold, lane, queued = [], recent = [], books = [], attention = [] } = data || {}
  const tracked = books.filter(b => showAll || b.state !== 'untracked' || b.mode !== 'off')

  return (
    <div className="p-6 max-w-6xl mx-auto space-y-6">
      <div className="flex items-center gap-3 flex-wrap">
        <Newspaper size={24} className="text-indigo-400" />
        <h1 className="text-2xl font-bold text-slate-100">NovelUpdates</h1>
        <span className={`px-2 py-0.5 rounded text-xs font-medium ${
          worker?.running ? 'bg-emerald-500/20 text-emerald-300' : 'bg-rose-500/20 text-rose-300'}`}>
          worker {worker?.running ? 'running' : 'not running'}
        </span>
        {!settings.nu_enabled && <span className="px-2 py-0.5 rounded text-xs bg-amber-500/20 text-amber-300">off in Settings</span>}
        <div className="flex-1" />
        <button className="btn btn-secondary text-xs inline-flex items-center gap-1" disabled={!!busy}
                title="Re-read our group page on NU (activates series NU has approved)"
                onClick={() => run('sync', () => api.nuSync())}>
          {busy === 'sync' ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />} Sync group
        </button>
        <button className="btn btn-secondary text-xs inline-flex items-center gap-1" disabled={!!busy}
                title="Search NU for every public book not looked up yet. Read-only: posts nothing, changes no mode."
                onClick={async () => {
                  const r = await run('find', () => api.nuFindAll())
                  if (r) setError(null)
                }}>
          {busy === 'find' ? <Loader2 size={12} className="animate-spin" /> : <Search size={12} />} Find all on NU
        </button>
      </div>

      {!worker?.running && (
        <Banner tone="rose">The worker (t9-scraper.service) isn&apos;t running — NU jobs queue here but nothing runs them.</Banner>
      )}
      {!data.site_base && (
        <Banner tone="rose">SITE_BASE_URL isn&apos;t set, so there are no public chapter links to post.</Banner>
      )}
      {hold && (
        <Banner tone="rose">
          <div className="flex items-center gap-2">
            <AlertTriangle size={14} />
            <span className="flex-1">NU posting is held {relativeTime(hold.at)}: {hold.reason}</span>
            <button className="btn btn-secondary text-xs" onClick={() => run('clear', () => api.nuClearHold())}>Clear hold</button>
          </div>
        </Banner>
      )}
      {error && <Banner tone="rose">{error}</Banner>}

      {attention.length > 0 && (
        <section>
          <h2 className="text-sm font-semibold text-slate-300 mb-2">Needs you ({attention.length})</h2>
          <div className="space-y-1">
            {attention.map(a => (
              <button key={a.book_id} onClick={() => setOpenBook(a.book_id)}
                      className="w-full text-left flex items-center gap-2 text-xs px-3 py-2 rounded bg-amber-500/5 border border-amber-700/30 hover:border-amber-600/60">
                <span className="text-slate-200 font-medium shrink-0">{a.title}</span>
                <span className="px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-300 shrink-0">
                  {HOLD_LABEL[a.kind] || STATE_LABEL[a.kind] || a.kind}
                </span>
                <span className="text-slate-400 truncate">{a.detail}</span>
              </button>
            ))}
          </div>
        </section>
      )}

      <section>
        <h2 className="text-sm font-semibold text-slate-300 mb-2">On the scraper</h2>
        {lane
          ? <NuTaskRow task={lane} onChanged={refresh} setError={setError} showBook onOpenBook={setOpenBook} />
          : <p className="text-xs text-slate-500">Nothing — {queued.length ? `${queued.length} queued` : 'idle'}.</p>}
        {queued.length > 0 && (
          <div className="space-y-1 mt-2">
            {queued.map(t => <NuTaskRow key={t.id} task={t} onChanged={refresh} setError={setError} showBook onOpenBook={setOpenBook} />)}
          </div>
        )}
      </section>

      <section>
        <div className="flex items-center gap-3 mb-2">
          <h2 className="text-sm font-semibold text-slate-300">Books</h2>
          <label className="text-xs text-slate-500 inline-flex items-center gap-1 ml-auto">
            <input type="checkbox" className="accent-indigo-500" checked={showAll} onChange={e => setShowAll(e.target.checked)} />
            show books not looked up
          </label>
        </div>
        <div className="card overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="text-slate-500 text-left">
              <tr>
                <th className="px-3 py-2 font-medium">Book</th>
                <th className="px-3 py-2 font-medium">State</th>
                <th className="px-3 py-2 font-medium">Mode</th>
                <th className="px-3 py-2 font-medium">From</th>
                <th className="px-3 py-2 font-medium">Last release</th>
                <th className="px-3 py-2 font-medium">To post</th>
                <th className="px-3 py-2 font-medium">NU</th>
              </tr>
            </thead>
            <tbody>
              {tracked.map(b => (
                <tr key={b.book_id} className="border-t border-slate-800 hover:bg-slate-800/40 cursor-pointer" onClick={() => setOpenBook(b.book_id)}>
                  <td className="px-3 py-1.5 text-slate-200">
                    {b.title}{!b.is_public && <span className="ml-1 text-slate-500">(private)</span>}
                  </td>
                  <td className="px-3 py-1.5">
                    <span className={`px-1.5 py-0.5 rounded ${STATE_CHIP[b.state] || ''}`}>{STATE_LABEL[b.state] || b.state}</span>
                    {b.hold_reason && <span className="ml-1 px-1.5 py-0.5 rounded bg-rose-500/20 text-rose-300">{HOLD_LABEL[b.hold_reason] || b.hold_reason}</span>}
                    {b.rate_limited_until && (
                      <span className="ml-1 px-1.5 py-0.5 rounded bg-sky-500/20 text-sky-300"
                            title="Backlog chapters NU won't find in the RSS feed are capped; new chapters still go">
                        backlog paused until {b.rate_limited_until.slice(11, 16)}
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-1.5 text-slate-400">{b.mode}</td>
                  <td className="px-3 py-1.5 text-slate-400">{b.start_chapter ? `c${b.start_chapter}` : '—'}</td>
                  <td className="px-3 py-1.5 text-slate-400">
                    {b.last_release_label ? `${b.last_release_label} · ${relativeTime(b.last_release_at)}` : '—'}
                  </td>
                  <td className="px-3 py-1.5 text-slate-400">{b.pending || ''}</td>
                  <td className="px-3 py-1.5" onClick={e => e.stopPropagation()}>
                    {b.nu_url && (
                      <a href={b.nu_url} target="_blank" rel="noreferrer" className="text-indigo-400 hover:text-indigo-300 inline-flex items-center gap-1">
                        {b.nu_title || b.nu_slug} <ExternalLink size={10} />
                      </a>
                    )}
                  </td>
                </tr>
              ))}
              {tracked.length === 0 && (
                <tr><td colSpan={7} className="px-3 py-6 text-center text-slate-500">
                  No book is on NU tracking yet — use <em>Find all on NU</em>, or open a book from Books → Actions → NovelUpdates.
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      {recent.length > 0 && (
        <section>
          <h2 className="text-sm font-semibold text-slate-300 mb-2">Recent</h2>
          <div className="space-y-1">
            {recent.map(t => <NuTaskRow key={t.id} task={t} onChanged={refresh} setError={setError} showBook onOpenBook={setOpenBook} />)}
          </div>
        </section>
      )}

      {openBook && <NovelUpdatesModal bookId={openBook} onClose={() => { setOpenBook(null); refresh() }} />}
    </div>
  )
}

function Banner({ tone, children }) {
  const cls = tone === 'rose'
    ? 'bg-rose-500/10 border-rose-700/50 text-rose-300'
    : 'bg-amber-500/10 border-amber-700/50 text-amber-200'
  return <div className={`px-3 py-2 rounded-lg border text-sm ${cls}`}>{children}</div>
}
