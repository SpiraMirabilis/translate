import { useState, useEffect } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { X, Loader2, Search, ExternalLink, Ban, RotateCcw, AlertTriangle, Check, Send, Eye } from 'lucide-react'
import { api } from '../../services/api'
import { relativeTime } from '../../lib/relativeTime'
import { TASK_STATUS_CHIP } from '../../lib/scrape'
import {
  STATE_LABEL, STATE_CHIP, HOLD_LABEL, RELEASE_CHIP, nuTaskLabel, nuTaskSummary, screenshotUrl,
} from '../../lib/novelupdates'

const OPEN = ['queued', 'running', 'blocked', 'cancelling']

/**
 * Per-book NovelUpdates posting: whether new chapters are posted and from
 * which chapter, which NU series the book is, the new-series draft (dry run,
 * then submit), and what has been posted. The work runs in the NU lane of
 * t9-scraper.service; this only records decisions, so it polls while a task
 * for the book is open.
 */
export default function NovelUpdatesModal({ bookId, onClose }) {
  const queryClient = useQueryClient()
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(null)
  const [mode, setMode] = useState(null)
  const [start, setStart] = useState('')
  const [linkUrl, setLinkUrl] = useState('')
  const [firstChapter, setFirstChapter] = useState('1')

  const { data, isPending } = useQuery({
    queryKey: ['book-nu', bookId],
    queryFn: () => api.getBookNu(bookId),
    refetchInterval: (q) =>
      (q.state.data?.tasks || []).some(t => OPEN.includes(t.status))
        || q.state.data?.state?.state === 'searching' ? 3000 : false,
  })

  useEffect(() => {
    if (!data) return
    setMode(data.state.mode)
    setStart(String(data.state.start_chapter ?? data.suggested_start ?? ''))
    if (data.first_chapter) setFirstChapter(String(data.first_chapter))
  }, [data?.state?.mode, data?.state?.start_chapter, data?.suggested_start])  // eslint-disable-line react-hooks/exhaustive-deps

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['book-nu', bookId] })
    queryClient.invalidateQueries({ queryKey: ['nu-overview'] })
  }
  const run = async (label, fn) => {
    setBusy(label); setError(null)
    try { await fn(); refresh() } catch (e) { setError(e.detail || e.message) } finally { setBusy(null) }
  }

  const st = data?.state
  const book = data?.book
  const spin = (label) => busy === label ? <Loader2 size={12} className="animate-spin" /> : null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="card w-full max-w-3xl max-w-[95vw] max-h-[92vh] flex flex-col shadow-2xl">
        <div className="flex items-center justify-between px-5 py-3 border-b border-slate-700 shrink-0">
          <div className="min-w-0">
            <h2 className="font-semibold text-slate-200">NovelUpdates</h2>
            <p className="text-xs text-slate-500 truncate">
              {book?.title}{book?.source_title ? ` · ${book.source_title}` : ''}
            </p>
          </div>
          <button className="btn-ghost p-1" onClick={onClose}><X size={16} /></button>
        </div>

        {isPending || !st ? (
          <div className="flex justify-center py-16"><Loader2 size={24} className="animate-spin text-indigo-400" /></div>
        ) : (
          <div className="overflow-y-auto px-5 py-4 space-y-5">
            {error && <Banner tone="rose">{error}</Banner>}
            {!data.enabled && <Banner tone="amber">NovelUpdates posting is off in Settings — nothing here runs until it&apos;s on.</Banner>}
            {data.hold && <Banner tone="rose">The whole NU lane is held: {data.hold.reason}. Fix it on the scraper, then clear it on the NovelUpdates page.</Banner>}
            {!book.is_public && <Banner tone="amber">This book is private, so its chapter links would 404 on NU.</Banner>}
            {st.hold_reason && (
              <Banner tone="rose">
                <div className="flex items-start gap-2">
                  <AlertTriangle size={14} className="mt-0.5 shrink-0" />
                  <div className="flex-1">
                    <div className="font-medium">{HOLD_LABEL[st.hold_reason] || st.hold_reason} — posting is stopped for this book</div>
                    {st.hold_detail && <div className="text-xs mt-0.5 text-rose-200/80">{st.hold_detail}</div>}
                  </div>
                  {!['check_nu'].includes(st.hold_reason) && (
                    <button className="btn btn-secondary text-xs" disabled={!!busy}
                            onClick={() => run('clear', () => api.updateBookNu(bookId, { clear_hold: true }))}>
                      {spin('clear')} Clear hold
                    </button>
                  )}
                </div>
              </Banner>
            )}

            {/* Status */}
            <div className="flex items-center gap-3 flex-wrap text-sm">
              <span className={`px-2 py-0.5 rounded text-xs font-medium ${STATE_CHIP[st.state] || ''}`}>{STATE_LABEL[st.state] || st.state}</span>
              {st.nu_url && (
                <a href={st.nu_url} target="_blank" rel="noreferrer" className="text-indigo-400 hover:text-indigo-300 inline-flex items-center gap-1 text-xs">
                  {book.nu_title || st.nu_slug} <ExternalLink size={11} />
                </a>
              )}
              {st.last_release_label && <span className="text-xs text-slate-400">last: {st.last_release_label} {relativeTime(st.last_release_at)}</span>}
              {data.pending > 0 && <span className="text-xs text-slate-400">{data.pending} to post</span>}
            </div>
            {st.last_error && !st.hold_reason && <p className="text-xs text-amber-300">{st.last_error}</p>}
            {st.rate_limited_until && (
              <p className="text-xs text-sky-300">
                Backlog rate limit reached: older chapters (which NU queues for manual approval) resume at {st.rate_limited_until.replace('T', ' ').slice(0, 16)}. New chapters still go out.
              </p>
            )}

            {/* Posting */}
            <Section title="Posting">
              <div className="flex items-end gap-3 flex-wrap">
                <label className="text-xs text-slate-400">
                  Mode
                  <select className="input mt-1" value={mode || 'off'} onChange={e => setMode(e.target.value)}>
                    <option value="off">Off</option>
                    <option value="auto">Auto — post every new chapter</option>
                  </select>
                </label>
                <label className="text-xs text-slate-400">
                  Post from chapter
                  <input className="input mt-1 w-28" type="number" min="1" value={start} onChange={e => setStart(e.target.value)} />
                </label>
                <button className="btn btn-primary text-xs inline-flex items-center gap-1" disabled={!!busy}
                        onClick={() => run('mode', () => api.updateBookNu(bookId, { mode, start_chapter: start ? parseInt(start, 10) : null }))}>
                  {spin('mode')} Save
                </button>
              </div>
              <p className="text-xs text-slate-500 mt-2">
                Nothing below this chapter is ever posted automatically — chapters already on NU (posted by hand) stay
                untouched. {['linked', 'new_series', 'series_submitted', 'first_release_sent'].includes(st.state)
                  ? <>NU doesn&apos;t have any release of ours for this series yet, so the suggestion is c1: it goes out first,
                      and the rest follow once NU approves it (about a day).</>
                  : <>The suggestion, c{data.suggested_start}, is the next chapter to be published.</>}
                {' '}Posting starts once the series is linked or active.
              </p>
            </Section>

            {/* Finding the series */}
            {['untracked', 'searching', 'linked', 'needs_review', 'new_series'].includes(st.state) && (
              <Section title="Series on NovelUpdates">
                <div className="flex items-center gap-2 flex-wrap">
                  <button className="btn btn-secondary text-xs inline-flex items-center gap-1"
                          disabled={!!busy || st.state === 'searching'}
                          onClick={() => run('search', () => api.nuSearch(bookId))}>
                    {spin('search') || <Search size={12} />} {st.state === 'searching' ? 'Searching…' : 'Search NU'}
                  </button>
                  <span className="text-xs text-slate-500">by the original title, in both scripts{st.search_names ? `: ${st.search_names.join(' / ')}` : ''}</span>
                </div>
                <div className="flex items-center gap-2 mt-3">
                  <input className="input flex-1 text-xs" placeholder="…or paste the NU series URL"
                         value={linkUrl} onChange={e => setLinkUrl(e.target.value)} />
                  <button className="btn btn-secondary text-xs" disabled={!linkUrl.trim() || !!busy}
                          onClick={() => run('link', () => api.nuPick(bookId, { url: linkUrl.trim() }))}>
                    {spin('link')} Link
                  </button>
                </div>
                {st.state === 'needs_review' && (
                  <div className="mt-3 space-y-1.5">
                    <p className="text-xs text-amber-300">NU returned near matches but none exactly. Pick the right one, or declare it new.</p>
                    {(st.candidates || []).map(c => (
                      <div key={c.slug} className="flex items-center gap-2 text-xs px-3 py-1.5 rounded bg-slate-800/50">
                        <a href={c.url} target="_blank" rel="noreferrer" className="text-slate-200 hover:text-indigo-300 inline-flex items-center gap-1">
                          {c.title} <ExternalLink size={10} />
                        </a>
                        <span className="text-slate-500 truncate flex-1">{(c.associatedNames || []).slice(0, 4).join(' · ')}</span>
                        <button className="btn btn-secondary text-xs" disabled={!!busy}
                                onClick={() => run('pick', () => api.nuPick(bookId, { url: c.url, title: c.title, id: c.id }))}>This one</button>
                      </div>
                    ))}
                    <button className="btn btn-secondary text-xs" disabled={!!busy}
                            onClick={() => run('new', () => api.nuPick(bookId, { new: true }))}>None of these — it&apos;s a new series</button>
                  </div>
                )}
              </Section>
            )}

            {st.state === 'new_series' && (
              <DraftEditor bookId={bookId} data={data} busy={busy} run={run} spin={spin} />
            )}

            {st.state === 'series_submitted' && (
              <Section title="First release">
                <p className="text-xs text-slate-400 mb-2">
                  NU approves a new series only after its first release. Until then the series has no public page,
                  so the lane finds it by its submitted title{data.pending_title ? <> (<em>{data.pending_title}</em>)</> : ''} in
                  NU&apos;s add-release form and posts by its id.
                  {st.mode === 'auto' ? ' On Auto, the first chapter goes out as soon as the id is known.' : ''}
                </p>
                {st.nu_series_id ? (
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-xs text-emerald-300">Found on NU: id {st.nu_series_id}</span>
                    <input className="input w-24 text-xs" type="number" min="1" value={firstChapter}
                           onChange={e => setFirstChapter(e.target.value)} />
                    <button className="btn btn-primary text-xs inline-flex items-center gap-1" disabled={!!busy || !firstChapter}
                            onClick={() => run('postfirst', () => api.nuPostFirstRelease(bookId, parseInt(firstChapter, 10)))}>
                      {spin('postfirst') || <Send size={12} />} Post c{firstChapter || '?'} now
                    </button>
                  </div>
                ) : (
                  <div className="space-y-1.5">
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-amber-300">
                        {(data.tasks || []).some(t => t.kind === 'release_search' && OPEN.includes(t.status))
                          ? 'Looking the series up on NU…' : 'The series’ NU id isn’t known yet.'}
                      </span>
                      <button className="btn btn-secondary text-xs inline-flex items-center gap-1" disabled={!!busy}
                              onClick={() => run('rsearch', () => api.nuReleaseSearch(bookId))}>
                        {spin('rsearch') || <Search size={12} />} Look it up again
                      </button>
                    </div>
                    {(st.candidates || []).map(c => (
                      <div key={c.id} className="flex items-center gap-2 text-xs px-3 py-1.5 rounded bg-slate-800/50">
                        <span className="text-slate-200 flex-1">{c.title}</span>
                        <span className="text-slate-500">id {c.id}</span>
                        <button className="btn btn-secondary text-xs" disabled={!!busy}
                                onClick={() => run('pickid', () => api.nuPick(bookId, { id: c.id }))}>This one</button>
                      </div>
                    ))}
                  </div>
                )}
                <details className="mt-3">
                  <summary className="text-xs text-slate-500 cursor-pointer">Posted the first chapter by hand instead?</summary>
                  <div className="flex items-center gap-2 mt-2">
                    <input className="input w-24 text-xs" type="number" min="1" value={firstChapter} onChange={e => setFirstChapter(e.target.value)} />
                    <button className="btn btn-secondary text-xs inline-flex items-center gap-1" disabled={!!busy || !firstChapter}
                            onClick={() => run('first', () => api.nuFirstRelease(bookId, parseInt(firstChapter, 10)))}>
                      {spin('first')} I posted c{firstChapter || '?'}
                    </button>
                  </div>
                </details>
              </Section>
            )}

            {st.state === 'first_release_sent' && (
              <p className="text-xs text-slate-400">
                First release {st.first_release_label} sent {relativeTime(st.first_release_at)}. NU moderates it (about a day);
                group syncs check every few hours after that, and posting resumes when the series shows up on our group page.
              </p>
            )}

            {/* Next and recent releases */}
            {(data.next?.length > 0 || data.releases?.length > 0) && (
              <Section title="Releases">
                {data.next?.length > 0 && st.mode === 'auto' && (
                  <p className="text-xs text-slate-500 mb-2">Next: {data.next.map(n => `c${n.chapter}`).join(', ')}{data.pending > data.next.length ? '…' : ''}</p>
                )}
                {data.next?.length > 0 && st.nu_slug && (
                  <button className="btn btn-secondary text-xs inline-flex items-center gap-1 mb-2" disabled={!!busy}
                          title="Fill NU's form for this chapter and screenshot it, without submitting"
                          onClick={() => run('rdry', () => api.nuReleaseDryRun(bookId, data.next[0].chapter))}>
                    {spin('rdry') || <Eye size={12} />} Dry-run c{data.next[0].chapter}
                  </button>
                )}
                <div className="space-y-1">
                  {data.releases.map(r => (
                    <div key={r.id} className="flex items-center gap-2 text-xs px-3 py-1.5 rounded bg-slate-800/50">
                      <span className={`px-1.5 py-0.5 rounded font-medium ${RELEASE_CHIP[r.status] || ''}`}>{r.status}</span>
                      <span className="text-slate-200">{r.label}</span>
                      <span className="text-slate-500 truncate flex-1" title={r.error || r.message}>{r.error || r.message}</span>
                      <span className="text-slate-600">{relativeTime(r.submitted_at || r.updated_at)}</span>
                      {['check', 'failed'].includes(r.status) && (
                        <>
                          <button className="btn btn-secondary text-xs inline-flex items-center gap-1" disabled={!!busy}
                                  title="It is on NU — mark it sent"
                                  onClick={() => run('sent', () => api.nuResolveRelease(bookId, r.chapter_number, 'sent'))}>
                            <Check size={11} /> On NU
                          </button>
                          <button className="btn btn-secondary text-xs inline-flex items-center gap-1" disabled={!!busy}
                                  title="It isn't on NU — post it again (force)"
                                  onClick={() => run('resend', () => api.nuResolveRelease(bookId, r.chapter_number, 'resend'))}>
                            <Send size={11} /> Resend
                          </button>
                        </>
                      )}
                    </div>
                  ))}
                </div>
              </Section>
            )}

            {data.tasks?.length > 0 && (
              <Section title="Recent NU jobs">
                <div className="space-y-1">
                  {data.tasks.map(t => <NuTaskRow key={t.id} task={t} onChanged={refresh} setError={setError} />)}
                </div>
              </Section>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

function Section({ title, children }) {
  return (
    <section>
      <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-500 mb-2">{title}</h3>
      {children}
    </section>
  )
}

function Banner({ tone, children }) {
  const cls = tone === 'rose'
    ? 'bg-rose-500/10 border-rose-700/50 text-rose-300'
    : 'bg-amber-500/10 border-amber-700/50 text-amber-200'
  return <div className={`px-3 py-2 rounded-lg border text-sm ${cls}`}>{children}</div>
}

const lines = (v) => (Array.isArray(v) ? v : []).join('\n')
const split = (s) => s.split(/[\n,]/).map(x => x.trim()).filter(Boolean)

function DraftEditor({ bookId, data, busy, run, spin }) {
  const st = data.state
  const vocab = data.vocab
  const [draft, setDraft] = useState(st.series_draft || {})
  const [dirty, setDirty] = useState(false)
  const [tagInput, setTagInput] = useState('')

  useEffect(() => { setDraft(st.series_draft || {}); setDirty(false) }, [st.series_draft])

  const set = (k, v) => { setDraft(d => ({ ...d, [k]: v })); setDirty(true) }
  const toggle = (k, name) => set(k, (draft[k] || []).includes(name)
    ? draft[k].filter(x => x !== name) : [...(draft[k] || []), name])
  const addTag = (name) => {
    const hit = (vocab?.tags || []).find(t => t.toLowerCase() === name.trim().toLowerCase())
    if (hit && !(draft.tags || []).includes(hit)) set('tags', [...(draft.tags || []), hit])
    setTagInput('')
  }
  const dry = st.draft_dry_run
  const canSubmit = data.draft_dry_run_current && !dirty

  return (
    <Section title="New series draft">
      <p className="text-xs text-slate-400 mb-3">
        NU doesn&apos;t list this book. Check the draft, save it, dry-run it (the scraper fills NU&apos;s form and
        screenshots it without submitting), then submit. Submit stays locked until this exact draft has dry-run.
      </p>
      {!vocab && (
        <Banner tone="amber">
          NU&apos;s genre and tag lists aren&apos;t cached yet — they&apos;re being fetched.{' '}
          <button className="underline" onClick={() => run('vocab', () => api.nuRefreshVocab())}>Fetch now</button>
        </Banner>
      )}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mt-2">
        <Field label="English title (NU's series name)">
          <input className="input" value={draft.title || ''} onChange={e => set('title', e.target.value)} />
        </Field>
        <Field label="Type">
          <select className="input" value={draft.type || ''} onChange={e => set('type', e.target.value)}>
            {['Web Novel', 'Light Novel', 'Published Novel'].map(t => <option key={t}>{t}</option>)}
          </select>
        </Field>
        <Field label="Associated names (raw title first, one per line)">
          <textarea className="input h-20" value={lines(draft.associatedNames)} onChange={e => set('associatedNames', split(e.target.value))} />
        </Field>
        <Field label="Description">
          <textarea className="input h-20" value={draft.description || ''} onChange={e => set('description', e.target.value)} />
        </Field>
        <Field label="Language">
          <select className="input" value={draft.language || ''} onChange={e => set('language', e.target.value || null)}>
            <option value="">—</option>
            {(vocab?.languages || []).map(l => <option key={l}>{l}</option>)}
          </select>
        </Field>
        <Field label="Authors (comma-separated)">
          <input className="input" value={(draft.authors || []).join(', ')} onChange={e => set('authors', split(e.target.value))} />
        </Field>
        <Field label="Status in country of origin">
          <input className="input" placeholder="1200 Chapters (Ongoing)" value={(draft.statusInCoo || []).join(', ')}
                 onChange={e => set('statusInCoo', split(e.target.value))} />
        </Field>
        <Field label="Cover URL">
          <input className="input" value={draft.coverUrl || ''} onChange={e => set('coverUrl', e.target.value || null)} />
        </Field>
      </div>

      <Field label={`Genres (${(draft.genres || []).length}, at least one)`} className="mt-3">
        <div className="flex flex-wrap gap-1">
          {(vocab?.genres || draft.genres || []).map(g => (
            <button key={g} onClick={() => toggle('genres', g)}
                    className={`px-2 py-0.5 rounded text-xs ${(draft.genres || []).includes(g) ? 'bg-indigo-600 text-white' : 'bg-slate-800 text-slate-400 hover:text-slate-200'}`}>
              {g}
            </button>
          ))}
        </div>
      </Field>
      <Field label="Tags" className="mt-3">
        <div className="flex flex-wrap gap-1 mb-1.5">
          {(draft.tags || []).map(t => (
            <span key={t} className="px-1.5 py-0.5 rounded text-xs bg-slate-700 text-slate-200 inline-flex items-center gap-1">
              {t}<button className="text-slate-400 hover:text-rose-300" onClick={() => toggle('tags', t)}><X size={10} /></button>
            </span>
          ))}
        </div>
        <input className="input text-xs" list={`nu-tags-${bookId}`} placeholder="Add an NU tag…" value={tagInput}
               onChange={e => setTagInput(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); addTag(tagInput) } }} />
        <datalist id={`nu-tags-${bookId}`}>{(vocab?.tags || []).map(t => <option key={t} value={t} />)}</datalist>
        {draft.unmatchedTags?.length > 0 && (
          <p className="text-xs text-slate-500 mt-1">Book tags with no NU equivalent by name: {draft.unmatchedTags.join(', ')}</p>
        )}
      </Field>

      <div className="flex items-center gap-2 flex-wrap mt-4">
        <button className="btn btn-secondary text-xs" disabled={!dirty || !!busy}
                onClick={() => run('save', () => api.nuSaveDraft(bookId, draft))}>{spin('save')} Save draft</button>
        <button className="btn btn-secondary text-xs" disabled={!!busy}
                onClick={() => run('reset', () => api.nuResetDraft(bookId))}>{spin('reset')} Rebuild from book</button>
        <button className="btn btn-secondary text-xs inline-flex items-center gap-1" disabled={dirty || !!busy}
                title={dirty ? 'Save the draft first' : 'Fill NU’s form and screenshot it, without submitting'}
                onClick={() => run('dry', () => api.nuDraftDryRun(bookId))}>{spin('dry') || <Eye size={12} />} Dry run</button>
        <button className="btn btn-primary text-xs inline-flex items-center gap-1" disabled={!canSubmit || !!busy}
                title={canSubmit ? 'Submit the new series to NU' : 'Dry-run this exact draft first'}
                onClick={() => { if (window.confirm('Submit this series to NovelUpdates?')) run('submit', () => api.nuDraftSubmit(bookId)) }}>
          {spin('submit') || <Send size={12} />} Submit to NU
        </button>
      </div>
      {dry && (
        <div className="mt-3 text-xs text-slate-400">
          Last dry run {relativeTime(st.draft_dry_run_at)}{data.draft_dry_run_current ? '' : ' (the draft changed since)'}
          {dry.screenshot && (
            <> · <a href={screenshotUrl(dry.screenshot)} target="_blank" rel="noreferrer" className="text-indigo-400 hover:text-indigo-300">screenshot</a></>
          )}
        </div>
      )}
    </Section>
  )
}

function Field({ label, children, className = '' }) {
  return (
    <label className={`block text-xs text-slate-400 ${className}`}>
      <span className="block mb-1">{label}</span>
      {children}
    </label>
  )
}

export function NuTaskRow({ task: t, onChanged, setError, showBook = false, onOpenBook }) {
  const act = async (fn) => {
    try { await fn(); onChanged() } catch (e) { setError?.(e.detail || e.message) }
  }
  const shot = t.result?.screenshot || t.result?.screenshots?.[1]
  return (
    <div className="flex items-center gap-2 text-xs px-3 py-1.5 rounded bg-slate-800/50 min-w-0">
      <span className={`px-1.5 py-0.5 rounded font-medium shrink-0 ${TASK_STATUS_CHIP[t.status] || ''}`}>{t.status}</span>
      {showBook && t.book_id && (
        <button className="text-slate-200 hover:text-indigo-300 truncate max-w-[14rem] shrink-0" onClick={() => onOpenBook?.(t.book_id)}>
          {t.book_title || `Book ${t.book_id}`}
        </button>
      )}
      <span className="text-slate-300 shrink-0">{nuTaskLabel(t)}</span>
      <span className="text-slate-500 truncate flex-1" title={nuTaskSummary(t)}>
        {nuTaskSummary(t)}
        {t.status === 'queued' && t.not_before && ` · after ${t.not_before.replace('T', ' ').slice(5, 16)}`}
      </span>
      {shot && (
        <a href={screenshotUrl(shot)} target="_blank" rel="noreferrer" className="text-indigo-400 hover:text-indigo-300 shrink-0">screenshot</a>
      )}
      {t.status === 'blocked' && t.vnc_url && (
        <a href={t.vnc_url} target="_blank" rel="noreferrer" className="text-amber-300 hover:text-amber-200 shrink-0 inline-flex items-center gap-1">
          Solve challenge <ExternalLink size={11} />
        </a>
      )}
      <span className="text-slate-600 shrink-0">{relativeTime(t.finished_at || t.started_at || t.created_at)}</span>
      {['queued', 'running', 'blocked'].includes(t.status) && (
        <button className="text-slate-500 hover:text-rose-300 shrink-0" title="Cancel"
                onClick={() => act(() => api.nuCancelTask(t.id))}><Ban size={12} /></button>
      )}
      {['failed', 'cancelled', 'skipped'].includes(t.status) && (
        <button className="text-slate-500 hover:text-indigo-300 shrink-0" title="Retry"
                onClick={() => act(() => api.nuRetryTask(t.id))}><RotateCcw size={12} /></button>
      )}
    </div>
  )
}
