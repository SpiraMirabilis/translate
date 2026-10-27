import { useState, useEffect } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { CloudDownload, Loader2, AlertTriangle, ExternalLink, Ban, Pause, Play } from 'lucide-react'
import { api } from '../services/api'
import { useSite } from '../App'
import { relativeTime } from '../lib/relativeTime'
import { MODE_CHIP, MODES } from '../lib/scrape'
import SourceModal, { TaskRow } from '../components/books/SourceModal'

/**
 * The scraper lane at a glance: what's on the scraper now (and whether it's
 * waiting on a human for a challenge), what's queued behind it, every book
 * with a source URL and its scrape state, and recent results.
 */
export default function Scraping() {
  const { site_name } = useSite()
  const queryClient = useQueryClient()
  const [openBook, setOpenBook] = useState(null)
  const [error, setError] = useState(null)
  const [showOff, setShowOff] = useState(false)
  const [pausing, setPausing] = useState(false)

  const { data, isPending } = useQuery({
    queryKey: ['scrape-overview'],
    queryFn: () => api.scrapeOverview(),
    refetchInterval: 5000,
  })

  useEffect(() => {
    document.title = `Scraping | ${site_name}`
    return () => { document.title = site_name }
  }, [site_name])

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['scrape-overview'] })

  if (isPending) {
    return <div className="flex justify-center py-16"><Loader2 size={28} className="animate-spin text-indigo-400" /></div>
  }

  const { settings, worker, paused, lane, queued = [], recent = [], books = [] } = data || {}

  const togglePause = async () => {
    setPausing(true)
    try {
      await (paused ? api.resumeScraping() : api.pauseScraping())
      setError(null)
      refresh()
    } catch (e) { setError(e.detail || e.message) } finally { setPausing(false) }
  }
  const visibleBooks = books.filter(b => showOff || b.mode !== 'off')
  const attention = (b) => b.mode !== 'off'
    && (b.hold_reason || b.unplaced_pending > 0 || b.consecutive_failures >= settings.failure_limit)

  return (
    <div className="p-6 max-w-6xl mx-auto space-y-6">
      <div className="flex items-center gap-3 flex-wrap">
        <CloudDownload size={24} className="text-indigo-400" />
        <h1 className="text-2xl font-bold text-slate-100">Scraping</h1>
        <span className={`px-2 py-0.5 rounded text-xs font-medium ${
          worker?.running ? 'bg-emerald-500/20 text-emerald-300' : 'bg-rose-500/20 text-rose-300'}`}
              title={worker?.age_seconds != null ? `last heartbeat ${worker.age_seconds}s ago` : 'no heartbeat'}>
          worker {worker?.running ? 'running' : 'not running'}
        </span>
        {!settings.enabled && <span className="px-2 py-0.5 rounded text-xs bg-amber-500/20 text-amber-300">disabled in Settings</span>}
        {paused && <span className="px-2 py-0.5 rounded text-xs font-medium bg-amber-500/20 text-amber-300">paused</span>}
        <button className={`btn text-xs inline-flex items-center gap-1 ${paused ? 'btn-primary' : 'btn-secondary'}`}
                disabled={pausing} onClick={togglePause}
                title={paused ? 'Let the worker submit jobs again' : 'Hold every scrape job, e.g. before restarting the worker or the scraper'}>
          {pausing ? <Loader2 size={12} className="animate-spin" /> : paused ? <Play size={12} /> : <Pause size={12} />}
          {paused ? 'Resume' : 'Pause'}
        </button>
        <span className="text-xs text-slate-500 ml-auto">
          Daily check at {String(settings.daily_hour).padStart(2, '0')}:00 · chunks of {settings.chunk_size} ·
          auto-translate gap {settings.default_gap} · {settings.reserved_slots} slot{settings.reserved_slots === 1 ? '' : 's'} kept free
        </span>
      </div>

      {!worker?.running && (
        <div className="px-3 py-2 rounded-lg bg-rose-500/10 border border-rose-700/50 text-sm text-rose-300">
          The scrape worker isn&apos;t running — tasks queue here but nothing runs them.
          Start it with <code className="text-rose-200">systemctl --user start t9-scraper.service</code>.
        </div>
      )}
      {paused && (
        <div className="px-3 py-2 rounded-lg bg-amber-500/10 border border-amber-700/50 text-sm text-amber-200">
          Paused {paused.paused_at ? relativeTime(paused.paused_at) : ''} — nothing new goes to the scraper and no
          translations auto-start; queued work waits.{' '}
          {lane
            ? <>Suspending the job on the scraper… (it resumes from its last page on Resume)</>
            : <>Nothing is on the scraper: it&apos;s safe to restart <code className="text-amber-100">t9-scraper.service</code> or the scraper.</>}
        </div>
      )}
      <SupportedSites known={data.supported_sites} onRefreshed={refresh} setError={setError} />

      {!settings.has_token && (
        <div className="px-3 py-2 rounded-lg bg-amber-500/10 border border-amber-700/50 text-sm text-amber-200">
          No scraper token configured — add it in Settings → Scraping.
        </div>
      )}
      {error && (
        <div className="px-3 py-2 rounded-lg bg-rose-500/10 border border-rose-700/50 text-sm text-rose-300">{error}</div>
      )}

      {/* The lane */}
      <section>
        <h2 className="text-sm font-semibold text-slate-300 mb-2">On the scraper</h2>
        {lane ? (
          <div className={`card p-4 border ${lane.status === 'blocked' ? 'border-amber-600/60' : 'border-slate-700'}`}>
            <div className="flex items-center gap-3 flex-wrap">
              <button className="font-semibold text-slate-100 hover:text-indigo-300" onClick={() => setOpenBook(lane.book_id)}>
                {lane.book_title || `Book ${lane.book_id}`}
              </button>
              <span className="text-sm text-slate-400">
                {lane.kind === 'toc' ? 'reading the TOC' : `${lane.reason === 'backfill' ? 'backfilling' : 'scraping new chapters'}`}
              </span>
              <span className="text-xs text-slate-500">started {relativeTime(lane.started_at)}</span>
              {lane.status === 'suspending' && <span className="text-xs text-amber-300">suspending for the pause…</span>}
              <div className="flex-1" />
              <button className="btn btn-secondary text-xs inline-flex items-center gap-1"
                      disabled={lane.status === 'cancelling'}
                      onClick={async () => { try { await api.cancelScrapeTask(lane.id); refresh() } catch (e) { setError(e.message) } }}>
                <Ban size={12} /> {lane.status === 'cancelling' ? 'Cancelling…' : 'Cancel'}
              </button>
            </div>
            {lane.kind === 'chapters' && (
              <div className="text-xs text-slate-400 mt-2">
                {lane.progress?.chapters ?? 0} / {lane.max_chapters ?? '?'} chapters
                {lane.progress?.lastTitle ? ` · ${lane.progress.lastTitle}` : ''}
              </div>
            )}
            {lane.status === 'blocked' && (
              <div className="mt-3 flex items-center gap-2 text-sm text-amber-200">
                <AlertTriangle size={15} />
                Waiting on a human to solve a challenge — everything else waits behind it.
                {lane.vnc_url && (
                  <a href={lane.vnc_url} target="_blank" rel="noreferrer"
                     className="btn btn-primary text-xs inline-flex items-center gap-1 ml-2">
                    Solve challenge <ExternalLink size={11} />
                  </a>
                )}
              </div>
            )}
          </div>
        ) : (
          <div className="text-sm text-slate-500">Idle.</div>
        )}
      </section>

      {queued.length > 0 && (
        <section>
          <h2 className="text-sm font-semibold text-slate-300 mb-2">Queued ({queued.length})</h2>
          <div className="space-y-1">
            {queued.map(t => (
              <TaskRow key={t.id} task={t} showBook onOpenBook={setOpenBook} onChanged={refresh} setError={setError} />
            ))}
          </div>
        </section>
      )}

      {/* Books */}
      <section>
        <div className="flex items-center gap-3 mb-2">
          <h2 className="text-sm font-semibold text-slate-300">Books</h2>
          <label className="text-xs text-slate-400 flex items-center gap-1.5">
            <input type="checkbox" checked={showOff} onChange={e => setShowOff(e.target.checked)} />
            Show books that are off ({books.filter(b => b.mode === 'off').length})
          </label>
        </div>
        {visibleBooks.length === 0 ? (
          <div className="text-sm text-slate-500">
            No books are being scraped yet. Open a book here (tick “Show books that are off”) or from
            Books → Actions → Source Scraping, link it to its TOC and pick a mode.
          </div>
        ) : (
          <div className="card overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-slate-500 border-b border-slate-700">
                  <th className="px-3 py-2">Book</th>
                  <th className="px-3 py-2">Mode</th>
                  <th className="px-3 py-2 text-right">TOC</th>
                  <th className="px-3 py-2 text-right">Held</th>
                  <th className="px-3 py-2 text-right">Translated</th>
                  <th className="px-3 py-2 text-right">New</th>
                  <th className="px-3 py-2">Checked</th>
                  <th className="px-3 py-2">State</th>
                </tr>
              </thead>
              <tbody>
                {visibleBooks.map(b => (
                  <tr key={b.book_id} onClick={() => setOpenBook(b.book_id)}
                      className={`border-b border-slate-800 last:border-0 cursor-pointer hover:bg-slate-800/50 ${attention(b) ? 'bg-amber-500/5' : ''}`}>
                    <td className="px-3 py-2 max-w-[18rem]">
                      <div className="text-slate-200 truncate">{b.title}</div>
                      <div className="text-[11px] text-slate-500 truncate">{b.source_url}</div>
                    </td>
                    <td className="px-3 py-2">
                      <span className={`px-2 py-0.5 rounded text-xs font-medium ${MODE_CHIP[b.mode]}`}>
                        {MODES.find(m => m.value === b.mode)?.label}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-right text-slate-300">{b.toc_count ?? '—'}</td>
                    <td className="px-3 py-2 text-right text-slate-300">{b.have_max ?? '—'}</td>
                    <td className="px-3 py-2 text-right text-slate-300">{b.translated_max ?? '—'}</td>
                    <td className="px-3 py-2 text-right text-sky-300">{b.linked ? (b.pending_count ?? '—') : ''}</td>
                    <td className="px-3 py-2 text-xs text-slate-500">{relativeTime(b.last_checked_at) || 'never'}</td>
                    <td className="px-3 py-2 text-xs max-w-[16rem]">
                      {b.unplaced_pending > 0 ? <span className="text-amber-300 truncate block">{b.unplaced_pending} unnumbered entr{b.unplaced_pending === 1 ? 'y' : 'ies'} to place</span>
                        : b.hold_reason ? <span className="text-amber-300 truncate block" title={b.hold_reason}>held: {b.hold_reason}</span>
                        : b.consecutive_failures >= settings.failure_limit ? <span className="text-rose-300 truncate block" title={b.last_error}>failing: {b.last_error}</span>
                          : b.last_error ? <span className="text-rose-300/80 truncate block" title={b.last_error}>{b.last_error}</span>
                            : b.warnings ? <span className="text-amber-300/80 truncate block" title={b.warnings}>⚠ {b.warnings}</span>
                            : b.site_supported === false ? <span className="text-slate-500">site not supported by the scraper</span>
                            : !b.linked ? <span className="text-slate-500">not linked</span>
                              : <span className="text-slate-500">ok</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {recent.length > 0 && (
        <section>
          <h2 className="text-sm font-semibold text-slate-300 mb-2">Recent</h2>
          <div className="space-y-1">
            {recent.map(t => (
              <TaskRow key={t.id} task={t} showBook onOpenBook={setOpenBook} onChanged={refresh} setError={setError} />
            ))}
          </div>
        </section>
      )}

      {openBook != null && <SourceModal bookId={openBook} onClose={() => { setOpenBook(null); refresh() }} />}
    </div>
  )
}

// The domains the scraper services (its /supported_sites, re-read daily by
// the worker). A site that appears onboards its books with a TOC check.
function SupportedSites({ known, onRefreshed, setError }) {
  const [busy, setBusy] = useState(false)
  const refresh = async () => {
    setBusy(true)
    try { await api.refreshScrapeSites(); onRefreshed() } catch (e) { setError(e.detail || e.message) } finally { setBusy(false) }
  }
  return (
    <div className="text-xs text-slate-400 flex items-center gap-2 flex-wrap">
      <span className="text-slate-500">Scraper supports:</span>
      {known?.sites?.length
        ? known.sites.map(d => <span key={d} className="px-1.5 py-0.5 rounded bg-slate-800 text-slate-300 font-mono">{d}</span>)
        : <span className="text-slate-500">unknown yet</span>}
      {known?.fetched_at && <span className="text-slate-600">· read {relativeTime(known.fetched_at)}</span>}
      <button className="btn-ghost text-xs text-indigo-400 hover:text-indigo-300 inline-flex items-center gap-1"
              disabled={busy} onClick={refresh}>
        {busy ? <Loader2 size={11} className="animate-spin" /> : null} Refresh
      </button>
    </div>
  )
}
