// Shared labels for the NovelUpdates UI (NovelUpdatesModal + the NovelUpdates
// page). Mirrors nu_sync.py: states, holds, release statuses and task kinds.

export const STATE_LABEL = {
  untracked:          'Not looked up',
  searching:          'Searching NU…',
  linked:             'On NU, not ours yet',
  needs_review:       'Needs review',
  new_series:         'New series (draft)',
  series_submitted:   'Series submitted',
  first_release_sent: 'Awaiting approval',
  active:             'Active',
}

export const STATE_CHIP = {
  untracked:          'bg-slate-500/20 text-slate-400',
  searching:          'bg-indigo-500/20 text-indigo-300',
  linked:             'bg-sky-500/20 text-sky-300',
  needs_review:       'bg-amber-500/20 text-amber-300',
  new_series:         'bg-amber-500/20 text-amber-300',
  series_submitted:   'bg-amber-500/20 text-amber-300',
  first_release_sent: 'bg-sky-500/20 text-sky-300',
  active:             'bg-emerald-500/20 text-emerald-300',
}

export const HOLD_LABEL = {
  check_nu:         'Check NU',
  refused:          'NU refused a release',
  bad_slug:         'Wrong NU series',
  approval_timeout: 'Approval overdue',
  failing:          'Keeps failing',
  not_public:       'Book is private',
}

export const RELEASE_CHIP = {
  queued:  'bg-slate-500/20 text-slate-300',
  posting: 'bg-indigo-500/20 text-indigo-300',
  sent:    'bg-emerald-500/20 text-emerald-300',
  exists:  'bg-emerald-500/20 text-emerald-300',
  manual:  'bg-emerald-500/20 text-emerald-300',
  check:   'bg-rose-500/20 text-rose-300',
  failed:  'bg-rose-500/20 text-rose-300',
}

export function nuTaskLabel(t) {
  const o = t.options || {}
  switch (t.kind) {
    case 'search':  return 'Search NU'
    case 'release_search': return `Find new series on NU${o.tries > 1 ? ` (try ${o.tries})` : ''}`
    case 'names':   return 'Add our names on NU'
    case 'release': return `${o.dry_run ? 'Dry run' : 'Post'} c${o.chapter}${o.force ? ' (force)' : ''}`
    case 'release_batch': {
      const cs = o.chapters || []
      return `Post c${cs[0]}–c${cs[cs.length - 1]} (${cs.length})${o.retry ? ` (retry ${o.retry})` : ''}`
    }
    case 'sync':    return 'Group sync'
    case 'series':  return o.dry_run ? 'New series (dry run)' : 'Submit new series'
    case 'vocab':   return 'Fetch NU vocabulary'
    case 'login':   return 'Log in'
    default:        return t.kind
  }
}

export function nuTaskSummary(t) {
  if (t.last_error && t.status !== 'done') return t.last_error
  const r = t.result || {}
  if (t.kind === 'search') {
    if (r.found) return `found “${r.series?.title}”${r.inOurGroup ? ' (ours)' : ''}`
    if (r.candidates?.length) return `${r.candidates.length} candidate(s), no exact match`
    if (t.status === 'done') return 'not on NU'
  }
  if (t.kind === 'release_search') {
    if (r.match) return `id ${r.match.id}`
    if (r.ambiguous) return `${r.hits?.length} series share the title`
    if (t.status === 'done') return r.hits?.length ? `${r.hits.length} near miss(es)` : 'not listed yet'
  }
  if (t.kind === 'names') {
    if (r.status === 'updated') return `added: ${(r.after || []).filter(n => !(r.before || []).includes(n)).join(', ')}`
    if (r.status === 'unchanged') return 'already listed'
  }
  if (t.kind === 'release_batch' && r.releases) {
    const n = r.releases.filter(e => ['submitted', 'exists', 'already_submitted'].includes(e.status)).length
    const bad = r.releases.find(e => e.status === 'failed')
    return `${n} posted` + (bad ? ` · stopped at ${bad.release}: ${bad.error || 'failed'}` : '')
  }
  if (t.kind === 'release' || t.kind === 'series') return r.message || r.status || ''
  if (t.kind === 'sync' && r.series) return `${r.series.length} series${r.added?.length ? `, ${r.added.length} new` : ''}`
  return ''
}

// The screenshot a dry run or a submit left behind, served through the admin API.
export const screenshotUrl = (name) => name ? `/api/nu/screenshots/${encodeURIComponent(name)}` : null
