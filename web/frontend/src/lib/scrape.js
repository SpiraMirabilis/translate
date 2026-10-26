// Shared labels for the scraping UI (SourceModal + the Scraping page).
// Mirrors scrape_sync.py: modes, priorities and task kinds.

export const MODES = [
  { value: 'off',   label: 'Off',                       hint: 'Not checked by the daily sweep.' },
  { value: 'queue', label: 'Queue only',                hint: 'New chapters are scraped into the queue; you start translating.' },
  { value: 'auto',  label: 'Queue + auto-translate',    hint: 'New chapters are queued and translated, if the gap to the last translated chapter is small.' },
]

export const MODE_CHIP = {
  off:   'bg-slate-500/20 text-slate-400',
  queue: 'bg-sky-500/20 text-sky-300',
  auto:  'bg-emerald-500/20 text-emerald-300',
}

export const TASK_STATUS_CHIP = {
  queued:     'bg-slate-500/20 text-slate-300',
  running:    'bg-indigo-500/20 text-indigo-300',
  blocked:    'bg-amber-500/20 text-amber-300',
  cancelling: 'bg-slate-500/20 text-slate-400',
  done:       'bg-emerald-500/20 text-emerald-300',
  skipped:    'bg-slate-500/20 text-slate-400',
  failed:     'bg-rose-500/20 text-rose-300',
  cancelled:  'bg-slate-500/20 text-slate-500',
}

export function taskLabel(t) {
  if (t.kind === 'toc') return { sweep: 'Daily TOC check', forced: 'TOC check (now)', onboard: 'TOC check (new site)' }[t.reason] || 'TOC check'
  if (t.kind === 'autostart') return 'Auto-translate start'
  const what = t.reason === 'backfill' ? 'Backfill' : 'Scrape new'
  return t.max_chapters ? `${what} (≤${t.max_chapters} ch)` : what
}

// One line describing a finished task's outcome.
export function taskSummary(t) {
  const s = t.result_summary
  if (t.last_error && t.status !== 'done') return t.last_error
  if (!s) return ''
  if (t.kind === 'toc') {
    if (s.error) return s.error
    const parts = [`${s.toc_count} entries`]
    if (s.new != null) parts.push(`${s.new} new`)
    if (s.catchup_queued) parts.push(`scraping ${s.catchup_queued}`)
    return parts.join(' · ')
  }
  if (t.kind === 'autostart') return s.started ? 'Translation started' : (s.reason || '')
  const parts = [`${(s.added || []).length} queued`]
  if (s.skipped_existing?.length) parts.push(`${s.skipped_existing.length} already present`)
  if (s.skipped_unnumbered?.length) parts.push(`${s.skipped_unnumbered.length} unnumbered skipped`)
  if (s.anomalies?.length) parts.push(`⚠ ${s.anomalies.length} warning(s)`)
  return parts.join(' · ')
}

export const OPEN_TASK_STATES = ['queued', 'running', 'blocked', 'cancelling']
