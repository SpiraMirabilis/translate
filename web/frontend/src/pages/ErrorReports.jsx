import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Flag, Trash2, ChevronDown, ChevronUp, Loader2, PenLine, BookOpen, Mail,
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
