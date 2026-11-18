import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Flag, Trash2, ChevronDown, ChevronUp, Loader2, PenLine, BookOpen, Mail,
  Wand2, Check, AlertTriangle, RotateCcw,
} from 'lucide-react'
import { api } from '../services/api'
import { useSite } from '../App'

const STATUS_COLORS = {
  new:       'bg-blue-500/20 text-blue-400',
  reviewed:  'bg-amber-500/20 text-amber-400',
  resolved:  'bg-emerald-500/20 text-emerald-400',
  dismissed: 'bg-slate-500/20 text-slate-400',
}

const STATUS_OPTIONS = ['new', 'reviewed', 'resolved', 'dismissed']

const TABS = [
  { value: 'new',       label: 'New' },
  { value: 'reviewed',  label: 'Reviewed' },
  { value: 'resolved',  label: 'Resolved' },
  { value: 'dismissed', label: 'Dismissed' },
  { value: null,        label: 'All' },
]

// Mirrors REPORT_TYPES in components/ReportErrorModal.jsx and the enum the
// public endpoint validates against.
const TYPE_LABELS = {
  wrong_term:     'Wrong name/term',
  mistranslation: 'Mistranslation',
  typo:           'Typo/grammar',
  formatting:     'Formatting',
  missing_text:   'Missing/duplicated',
  other:          'Other',
}
const TYPE_COLORS = {
  wrong_term:     'bg-fuchsia-500/20 text-fuchsia-300',
  mistranslation: 'bg-rose-500/20 text-rose-300',
  typo:           'bg-sky-500/20 text-sky-300',
  formatting:     'bg-teal-500/20 text-teal-300',
  missing_text:   'bg-orange-500/20 text-orange-300',
  other:          'bg-slate-500/20 text-slate-300',
}

/**
 * Where to go to act on a report.
 *
 * With a chapter and a quote this is the Chapter Editor already scrolled to the
 * reported string — the editor consumes `search`/`searchScope` from the URL,
 * the same deep link GlobalSearchModal builds. Without a quote it's just the
 * chapter; a book-wide report goes to the book.
 */
function targetLink(report) {
  if (!report.book_id) return null
  if (report.chapter_number == null) return `/books/${report.book_id}`
  const base = `/books/${report.book_id}/chapters/${report.chapter_number}/edit`
  if (!report.quote) return base
  const params = new URLSearchParams({ search: report.quote, searchScope: 'translated' })
  return `${base}?${params.toString()}`
}

const VERDICTS = {
  fix:              { label: 'Fix proposed',     cls: 'bg-emerald-500/20 text-emerald-300' },
  no_change:        { label: 'No change needed', cls: 'bg-slate-500/20 text-slate-300' },
  unclear:          { label: 'Unclear report',   cls: 'bg-amber-500/20 text-amber-300' },
  needs_other_tool: { label: 'Needs another tool', cls: 'bg-fuchsia-500/20 text-fuchsia-300' },
}

function Diff({ segments }) {
  return (
    <span className="whitespace-pre-wrap">
      {(segments || []).map(([op, text], i) => (
        op === 'del'
          ? <del key={i} className="bg-rose-500/20 text-rose-300 decoration-rose-400/70">{text}</del>
          : op === 'ins'
            ? <ins key={i} className="bg-emerald-500/20 text-emerald-300 no-underline">{text}</ins>
            : <span key={i} className="text-slate-300">{text}</span>
      ))}
    </span>
  )
}

/**
 * The stored auto-fix proposal for one report (web/services/report_autofix.py):
 * pick edits / footnotes / gender corrections, then apply. Keyed by the
 * proposal's created_at, so a re-run resets the selections.
 */
function AutofixPanel({ report, onDone, onError }) {
  const result = report.autofix_result || {}
  const applied = result.applied
  const [edits, setEdits] = useState(() => new Set((result.edits || []).map(e => e.id)))
  const [footnotes, setFootnotes] = useState(() => Object.fromEntries(
    (result.footnotes || []).map(f => [f.id, { checked: true, anchor: f.anchor, body: f.body }])))
  const [entities, setEntities] = useState(() => new Set())
  const [notes, setNotes] = useState(result.explanation || '')
  const [busy, setBusy] = useState(false)

  const toggle = (set, setter, id) => setter(() => {
    const next = new Set(set)
    next.has(id) ? next.delete(id) : next.add(id)
    return next
  })

  const chosenFootnotes = Object.entries(footnotes).filter(([, f]) => f.checked)
  const nothing = edits.size === 0 && chosenFootnotes.length === 0 && entities.size === 0

  const apply = async () => {
    setBusy(true)
    try {
      await api.applyErrorReportFix(report.id, {
        edit_ids: [...edits],
        footnotes: chosenFootnotes.map(([id, f]) => ({ id: Number(id), anchor: f.anchor, body: f.body })),
        entity_update_ids: [...entities],
        notes,
      })
      onDone()
    } catch (err) {
      onError(err.status === 409
        ? 'The chapter changed since this proposal was made — re-run Auto-fix.'
        : (err.detail || err.message || 'Could not apply the fix.'))
    } finally {
      setBusy(false)
    }
  }

  const dismiss = async () => {
    setBusy(true)
    try {
      await api.updateErrorReport(report.id, { status: 'dismissed', admin_notes: notes })
      onDone()
    } catch (err) {
      onError(err.detail || err.message || 'Could not update the report.')
    } finally {
      setBusy(false)
    }
  }

  if (report.autofix_status === 'failed') {
    return (
      <div className="rounded-lg border border-rose-700/50 bg-rose-500/10 px-3 py-2 text-sm text-rose-300 flex items-center gap-2">
        <AlertTriangle size={14} className="shrink-0" />
        <span>Auto-fix failed{result.model ? ` (${result.model})` : ''}: {result.error || 'unknown error'}</span>
      </div>
    )
  }

  const verdict = VERDICTS[result.verdict] || VERDICTS.unclear
  const rejected = result.rejected || {}
  const rejectedCount = (rejected.edits?.length || 0) + (rejected.footnotes?.length || 0)
    + (rejected.entity_updates?.length || 0)

  return (
    <div className="rounded-lg border border-indigo-700/40 bg-indigo-500/5 p-3 space-y-3">
      <div className="flex items-center gap-2 flex-wrap">
        <Wand2 size={14} className="text-indigo-400" />
        <span className={`px-2 py-0.5 rounded text-xs font-medium ${verdict.cls}`}>{verdict.label}</span>
        <span className="text-xs text-slate-500">{result.model}{result.research ? ' · with research' : ''} · {result.created_at}</span>
        {applied && (
          <span className="px-2 py-0.5 rounded text-xs font-medium bg-emerald-500/20 text-emerald-300 inline-flex items-center gap-1">
            <Check size={11} /> Applied {applied.at}
          </span>
        )}
      </div>

      {result.explanation && <p className="text-sm text-slate-200">{result.explanation}</p>}
      {result.other_action && (
        <p className="text-sm text-fuchsia-300"><span className="text-slate-500">Needs: </span>{result.other_action}</p>
      )}
      {result.untracked_footnotes && (
        <p className="text-xs text-amber-300">
          This chapter's footnotes predate the footnote table — edits keep them in place, but no new footnote can be added until <code>backfill_footnotes.py --book-id {report.book_id}</code> runs.
        </p>
      )}

      {(result.edits || []).length > 0 && (
        <div className="space-y-1.5">
          <div className="text-xs text-slate-500">Edits</div>
          {result.edits.map(e => (
            <label key={e.id} className="flex items-start gap-2 text-sm cursor-pointer">
              <input type="checkbox" className="mt-1" disabled={!!applied}
                     checked={applied ? applied.edits.includes(e.id) : edits.has(e.id)}
                     onChange={() => toggle(edits, setEdits, e.id)} />
              <div className="min-w-0 flex-1">
                <div className="text-xs text-slate-500">
                  T{e.line} · {e.kind === 'delete' ? 'delete line' : e.kind === 'split' ? 'split line' : 'edit'}
                  {e.reason ? ` — ${e.reason}` : ''}
                </div>
                <div className="leading-relaxed"><Diff segments={e.diff} /></div>
              </div>
            </label>
          ))}
        </div>
      )}

      {(result.footnotes || []).length > 0 && (
        <div className="space-y-2">
          <div className="text-xs text-slate-500">Footnotes</div>
          {result.footnotes.map(f => {
            const st = footnotes[f.id] || {}
            return (
              <div key={f.id} className="flex items-start gap-2">
                <input type="checkbox" className="mt-2" disabled={!!applied} checked={!!st.checked}
                       onChange={() => setFootnotes(s => ({ ...s, [f.id]: { ...st, checked: !st.checked } }))} />
                <div className="flex-1 min-w-0 space-y-1">
                  <div className="text-xs text-slate-400 whitespace-pre-wrap">{f.preview}</div>
                  <div className="flex gap-2 items-center flex-wrap">
                    <input className="input text-xs py-1 w-56" value={st.anchor ?? ''} disabled={!!applied}
                           title="Anchor: the exact words the marker attaches to"
                           onChange={e => setFootnotes(s => ({ ...s, [f.id]: { ...st, anchor: e.target.value } }))} />
                    <span className="text-xs text-slate-500">[{f.number}]{f.source_term ? ` · ${f.source_term}` : ''}</span>
                    {(f.warnings || []).map(w => (
                      <span key={w} className="px-1.5 py-0.5 rounded text-[11px] bg-amber-500/20 text-amber-300">{w}</span>
                    ))}
                  </div>
                  <textarea className="input text-sm w-full" rows={2} value={st.body ?? ''} disabled={!!applied}
                            onChange={e => setFootnotes(s => ({ ...s, [f.id]: { ...st, body: e.target.value } }))} />
                </div>
              </div>
            )
          })}
        </div>
      )}

      {(result.entity_updates || []).length > 0 && (
        <div className="space-y-1">
          <div className="text-xs text-slate-500">Glossary gender (opt-in)</div>
          {result.entity_updates.map(u => (
            <label key={u.id} className="flex items-center gap-2 text-sm cursor-pointer">
              <input type="checkbox" disabled={!!applied} checked={entities.has(u.id)}
                     onChange={() => toggle(entities, setEntities, u.id)} />
              <span className="text-slate-200">{u.translation} ({u.untranslated})</span>
              <span className="text-slate-500">{u.old || 'unset'} → <span className="text-emerald-300">{u.value}</span></span>
              {u.reason && <span className="text-xs text-slate-500 truncate">— {u.reason}</span>}
            </label>
          ))}
        </div>
      )}

      {rejectedCount > 0 && (
        <details className="text-xs text-slate-500">
          <summary className="cursor-pointer">{rejectedCount} proposal{rejectedCount !== 1 ? 's' : ''} failed validation</summary>
          <ul className="mt-1 space-y-1 pl-4 list-disc">
            {['edits', 'footnotes', 'entity_updates'].flatMap(k => (rejected[k] || []).map((r, i) => (
              <li key={`${k}${i}`}>
                <span className="text-amber-300">{r.why}</span>
                {' — '}{k === 'edits' ? `T${r.line}: “${r.find}” → “${r.replace}”`
                  : k === 'footnotes' ? `“${r.anchor}”: ${r.body}` : `entity ${r.entity_id} → ${r.value}`}
              </li>
            )))}
          </ul>
        </details>
      )}

      {applied ? (
        <div className="text-xs text-slate-400">
          {applied.edits.length} edit{applied.edits.length !== 1 ? 's' : ''},{' '}
          {applied.footnotes.length} footnote{applied.footnotes.length !== 1 ? 's' : ''}
          {applied.entity_updates.length ? `, ${applied.entity_updates.map(g => `${g.translation} → ${g.value}`).join(', ')}` : ''}
          {applied.footnotes_skipped?.length ? ` · skipped: ${applied.footnotes_skipped.map(f => `${f.anchor} (${f.why})`).join('; ')}` : ''}
          {' '}— the pre-fix text is in the chapter's History.
        </div>
      ) : (
        <>
          <div>
            <div className="text-xs text-slate-500 mb-1">Notes to save with the report</div>
            <textarea className="input text-sm w-full" rows={2} value={notes} onChange={e => setNotes(e.target.value)} />
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            <button onClick={apply} disabled={busy || nothing}
                    className="btn btn-primary text-xs inline-flex items-center gap-1.5 disabled:opacity-50">
              {busy ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />} Apply &amp; resolve
            </button>
            {result.verdict !== 'fix' && (
              <button onClick={dismiss} disabled={busy} className="btn btn-secondary text-xs">
                Mark dismissed with these notes
              </button>
            )}
          </div>
        </>
      )}
    </div>
  )
}

export default function ErrorReports() {
  const { site_name } = useSite()
  const queryClient = useQueryClient()
  const [filter, setFilter] = useState('new')
  const [expanded, setExpanded] = useState(null)
  const [editingNotes, setEditingNotes] = useState({})
  const [actionError, setActionError] = useState(null)

  const { data, isPending: loading } = useQuery({
    queryKey: ['error-reports', filter],
    queryFn: () => api.listErrorReports(filter),
    // Poll while an auto-fix is running; the model call takes a minute or two.
    refetchInterval: (query) =>
      (query.state.data?.items || []).some(r => r.autofix_status === 'running') ? 3000 : false,
  })
  const items = data?.items || []

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['error-reports'] })

  useEffect(() => {
    document.title = `Error Reports | ${site_name}`
    return () => { document.title = site_name }
  }, [site_name])

  const toggleExpand = (id) => setExpanded(cur => (cur === id ? null : id))

  const handleStatusChange = async (id, status) => {
    setActionError(null)
    try {
      await api.updateErrorReport(id, { status })
      invalidate()
    } catch (err) {
      setActionError(err.detail || err.message || 'Could not update the report.')
    }
  }

  const handleSaveNotes = async (id) => {
    setActionError(null)
    try {
      await api.updateErrorReport(id, { admin_notes: editingNotes[id] ?? '' })
      setEditingNotes(n => { const out = { ...n }; delete out[id]; return out })
      invalidate()
    } catch (err) {
      setActionError(err.detail || err.message || 'Could not save the note.')
    }
  }

  const handleAutofix = async (id) => {
    setActionError(null)
    try {
      await api.autofixErrorReport(id)
      invalidate()
    } catch (err) {
      setActionError(err.detail || err.message || 'Could not start auto-fix.')
    }
  }

  const handleDelete = async (id) => {
    if (!confirm('Delete this error report?')) return
    setActionError(null)
    try {
      await api.deleteErrorReport(id)
      invalidate()
    } catch (err) {
      setActionError(err.detail || err.message || 'Could not delete the report.')
    }
  }

  return (
    <div className="p-6 max-w-5xl mx-auto">
      {/* Header */}
      <div className="flex items-center gap-3 mb-6">
        <Flag size={24} className="text-indigo-400" />
        <h1 className="text-2xl font-bold text-slate-100">Error Reports</h1>
      </div>

      {/* Filter tabs */}
      <div className="flex gap-1 mb-6 flex-wrap">
        {TABS.map(tab => (
          <button
            key={tab.label}
            onClick={() => setFilter(tab.value)}
            className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
              filter === tab.value
                ? 'bg-indigo-600 text-white'
                : 'bg-slate-800 text-slate-400 hover:text-slate-200 hover:bg-slate-700'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {actionError && (
        <div className="mb-4 px-3 py-2 rounded-lg bg-rose-500/10 border border-rose-700/50 text-sm text-rose-300 flex items-center justify-between gap-3">
          <span>{actionError}</span>
          <button onClick={() => setActionError(null)} className="text-rose-400 hover:text-rose-300 text-xs shrink-0">Dismiss</button>
        </div>
      )}

      {loading ? (
        <div className="flex justify-center py-16">
          <Loader2 size={28} className="animate-spin text-indigo-400" />
        </div>
      ) : items.length === 0 ? (
        <div className="text-center py-16 text-slate-500">
          <Flag size={40} className="mx-auto mb-3 opacity-30" />
          <p>No error reports{filter ? ` with status "${filter}"` : ''}</p>
        </div>
      ) : (
        <div className="space-y-2">
          {items.map(report => {
            const link = targetLink(report)
            return (
              <div key={report.id} className="card border border-slate-700 rounded-lg overflow-hidden">
                {/* Row header */}
                <button
                  onClick={() => toggleExpand(report.id)}
                  className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-slate-800/50 transition-colors"
                >
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-semibold text-slate-100 truncate">
                        {report.book_title || `Book ${report.book_id}`}
                      </span>
                      <span className="text-xs text-slate-400">
                        {report.chapter_number == null ? 'Book-wide' : `Ch ${report.chapter_number}`}
                      </span>
                      <span className={`px-2 py-0.5 rounded text-xs font-medium ${TYPE_COLORS[report.report_type] || TYPE_COLORS.other}`}>
                        {TYPE_LABELS[report.report_type] || report.report_type}
                      </span>
                      <span className={`px-2 py-0.5 rounded text-xs font-medium ${STATUS_COLORS[report.status] || STATUS_COLORS.new}`}>
                        {report.status}
                      </span>
                    </div>
                    <div className="flex items-center gap-3 text-xs text-slate-500 mt-1 min-w-0">
                      <span className="truncate">{report.problem}</span>
                      <span className="shrink-0">{report.created_at?.replace('T', ' ').slice(0, 16)}</span>
                    </div>
                  </div>
                  {expanded === report.id
                    ? <ChevronUp size={16} className="text-slate-500" />
                    : <ChevronDown size={16} className="text-slate-500" />}
                </button>

                {/* Expanded details */}
                {expanded === report.id && (
                  <div className="px-4 pb-4 pt-1 border-t border-slate-700 space-y-3">
                    {report.quote && (
                      <div>
                        <div className="text-xs text-slate-500 mb-1">Reported text</div>
                        <blockquote className="border-l-2 border-indigo-500/60 pl-3 py-1 text-sm text-slate-200 whitespace-pre-wrap">
                          {report.quote}
                        </blockquote>
                      </div>
                    )}

                    <div>
                      <div className="text-xs text-slate-500 mb-1">What&apos;s wrong</div>
                      <p className="text-sm text-slate-200 whitespace-pre-wrap">{report.problem}</p>
                    </div>

                    {report.suggested_fix && (
                      <div>
                        <div className="text-xs text-slate-500 mb-1">Suggested fix</div>
                        <p className="text-sm text-slate-200 whitespace-pre-wrap">{report.suggested_fix}</p>
                      </div>
                    )}

                    {report.reporter_email && (
                      <div className="text-sm">
                        <span className="text-slate-500">Reporter: </span>
                        <a href={`mailto:${report.reporter_email}`}
                           className="text-indigo-400 hover:text-indigo-300 inline-flex items-center gap-1">
                          <Mail size={12} /> {report.reporter_email}
                        </a>
                      </div>
                    )}

                    {/* Admin notes */}
                    <div>
                      <div className="text-xs text-slate-500 mb-1">Notes</div>
                      <textarea
                        value={editingNotes[report.id] ?? report.admin_notes ?? ''}
                        onChange={(e) => setEditingNotes(n => ({ ...n, [report.id]: e.target.value }))}
                        rows={2}
                        className="input text-sm w-full"
                        placeholder="What you did about it…"
                      />
                      {editingNotes[report.id] !== undefined && (
                        <button onClick={() => handleSaveNotes(report.id)}
                                className="btn btn-secondary text-xs mt-2">
                          Save Notes
                        </button>
                      )}
                    </div>

                    {/* Auto-fix proposal */}
                    {report.autofix_status === 'running' && (
                      <div className="text-sm text-indigo-300 inline-flex items-center gap-2">
                        <Loader2 size={14} className="animate-spin" />
                        Asking {report.autofix_result?.model || 'the model'}… (reads the whole chapter; a minute or two)
                      </div>
                    )}
                    {report.autofix_status && report.autofix_status !== 'running' && report.autofix_result && (
                      <AutofixPanel
                        key={report.autofix_result.created_at || report.autofix_result.error}
                        report={report}
                        onDone={invalidate}
                        onError={setActionError}
                      />
                    )}

                    {/* Actions */}
                    <div className="flex items-center gap-2 flex-wrap pt-1">
                      {link && (
                        <Link
                          to={link}
                          className="btn btn-primary text-xs inline-flex items-center gap-1.5"
                          title={report.quote
                            ? 'Open the chapter with the reported text found'
                            : 'Open the chapter'}
                        >
                          {report.chapter_number == null
                            ? (<><BookOpen size={13} /> Open book</>)
                            : (<><PenLine size={13} /> Jump to the problem</>)}
                        </Link>
                      )}
                      {report.chapter_number != null && (
                        <button
                          onClick={() => handleAutofix(report.id)}
                          disabled={report.autofix_status === 'running'}
                          className="btn btn-secondary text-xs inline-flex items-center gap-1.5 disabled:opacity-50"
                          title="Have the model read the chapter and propose a fix — nothing changes until you apply it"
                        >
                          {report.autofix_status === 'running'
                            ? <Loader2 size={13} className="animate-spin" />
                            : report.autofix_status ? <RotateCcw size={13} /> : <Wand2 size={13} />}
                          {report.autofix_status && report.autofix_status !== 'running' ? 'Re-run auto-fix' : 'Auto-fix'}
                        </button>
                      )}
                      <div className="flex-1" />
                      {STATUS_OPTIONS.filter(s => s !== report.status).map(s => (
                        <button
                          key={s}
                          onClick={() => handleStatusChange(report.id, s)}
                          className="btn btn-secondary text-xs capitalize"
                        >
                          {s === 'new' ? 'Reopen' : `Mark ${s}`}
                        </button>
                      ))}
                      <button
                        onClick={() => handleDelete(report.id)}
                        className="btn btn-danger text-xs inline-flex items-center gap-1.5"
                      >
                        <Trash2 size={13} /> Delete
                      </button>
                    </div>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
