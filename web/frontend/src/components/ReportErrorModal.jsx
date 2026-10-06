import { useEffect, useMemo, useRef, useState } from 'react'
import { X, Loader2, CheckCircle, Send, Search } from 'lucide-react'
import { useTurnstile } from '../hooks/useTurnstile'
import { publicApi } from '../services/api'
import { loadIdentity } from './CommentForm'

export const REPORT_TYPES = [
  { id: 'wrong_term', label: 'Wrong name or term' },
  { id: 'mistranslation', label: 'Mistranslation' },
  { id: 'typo', label: 'Typo or grammar' },
  { id: 'formatting', label: 'Formatting or layout' },
  { id: 'missing_text', label: 'Missing or duplicated text' },
  { id: 'other', label: 'Something else' },
]

const QUOTE_MAX = 500
const TEXT_MAX = 2000

/**
 * Chapter picker: "Book-wide issue" plus every chapter, filtered as you type.
 *
 * Deliberately not components/ComboBox.jsx — that one is hard-styled for the
 * dark admin surface and commits free text, while this needs the reader's
 * light/sepia themes and a chapter *number* (or null) as its value.
 */
function ChapterPicker({ value, onChange, chapters, tokens, inputId }) {
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const boxRef = useRef(null)
  const listRef = useRef(null)

  const options = useMemo(() => {
    const all = [{ num: null, label: 'Book-wide issue' }].concat(
      (chapters || []).map(c => ({
        num: c.chapter,
        label: `Ch ${c.chapter}${c.title ? ` — ${c.title}` : ''}`,
      })),
    )
    const q = query.trim().toLowerCase()
    if (!q) return all
    return all.filter(o => o.label.toLowerCase().includes(q))
  }, [chapters, query])

  const selectedLabel = useMemo(() => {
    if (value == null) return 'Book-wide issue'
    const hit = (chapters || []).find(c => c.chapter === value)
    return `Ch ${value}${hit?.title ? ` — ${hit.title}` : ''}`
  }, [value, chapters])

  // Close on outside click, committing nothing (the value only changes on an
  // explicit pick — free text here would mean an unresolvable chapter).
  useEffect(() => {
    if (!open) return
    const onDocClick = (e) => {
      if (boxRef.current && !boxRef.current.contains(e.target)) {
        setOpen(false)
        setQuery('')
      }
    }
    document.addEventListener('mousedown', onDocClick)
    return () => document.removeEventListener('mousedown', onDocClick)
  }, [open])

  // Keep the active row in view while arrowing through a 1800-chapter book.
  useEffect(() => {
    if (!open || !listRef.current) return
    const el = listRef.current.children[active]
    if (el) el.scrollIntoView({ block: 'nearest' })
  }, [active, open])

  const pick = (opt) => {
    onChange(opt.num)
    setOpen(false)
    setQuery('')
  }

  const onKeyDown = (e) => {
    if (e.key === 'Escape') {
      if (open) { e.stopPropagation(); setOpen(false); setQuery('') }
      return
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      if (!open) { setOpen(true); setActive(0); return }
      setActive(a => Math.min(a + 1, options.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActive(a => Math.max(a - 1, 0))
    } else if (e.key === 'Enter' && open && options[active]) {
      e.preventDefault()
      pick(options[active])
    }
  }

  return (
    <div className="relative" ref={boxRef}>
      <input
        id={inputId}
        type="text"
        value={open ? query : selectedLabel}
        placeholder="Search chapters…"
        onFocus={() => { setOpen(true); setActive(0) }}
        onChange={(e) => { setQuery(e.target.value); setOpen(true); setActive(0) }}
        onKeyDown={onKeyDown}
        className={`w-full rounded-lg border px-3 py-2 text-sm ${tokens.input}`}
        autoComplete="off"
        role="combobox"
        aria-expanded={open}
        aria-controls="report-chapter-list"
      />
      <Search size={15} className={`absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none ${tokens.muted}`} />
      {open && (
        <ul
          id="report-chapter-list"
          ref={listRef}
          role="listbox"
          className={`absolute z-10 mt-1 max-h-56 w-full overflow-y-auto rounded-lg border shadow-lg ${tokens.dropdown}`}
        >
          {options.length === 0 && (
            <li className={`px-3 py-2 text-sm ${tokens.muted}`}>No matching chapter</li>
          )}
          {options.map((opt, i) => (
            <li
              key={opt.num == null ? 'book' : opt.num}
              role="option"
              aria-selected={opt.num === value}
              onMouseEnter={() => setActive(i)}
              onMouseDown={(e) => { e.preventDefault(); pick(opt) }}
              className={`cursor-pointer truncate px-3 py-2 text-sm ${
                i === active ? tokens.optionActive : ''
              } ${opt.num == null ? 'font-medium' : ''}`}
            >
              {opt.label}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/**
 * "Report an error" — shared by the Reader and the public book page.
 *
 * The optional quote is the point of the form: a chapter number alone costs a
 * full re-read to act on, while a quoted string becomes a Chapter Editor deep
 * link in the admin queue. The Reader pre-fills it from the reader's selection;
 * everything else works fine without it.
 *
 * Props:
 *   chapters       - [{ chapter, title }] the host page already holds (no fetch)
 *   defaultChapter - chapter number to preselect, or null for "Book-wide issue"
 *   initialQuote   - text the reader highlighted, if any
 *   theme          - 'dark' | 'light' | 'sepia'
 */
export default function ReportErrorModal({
  open, onClose, bookId, bookTitle, chapters = [],
  defaultChapter = null, initialQuote = '', theme = 'dark',
}) {
  const [chapterNumber, setChapterNumber] = useState(defaultChapter)
  const [reportType, setReportType] = useState('')
  const [quote, setQuote] = useState(initialQuote)
  const [problem, setProblem] = useState('')
  const [suggestedFix, setSuggestedFix] = useState('')
  // Returning commenters already told us their address once.
  const [email, setEmail] = useState(() => loadIdentity()?.email || '')
  const [siteKey, setSiteKey] = useState('')
  const [interacted, setInteracted] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState(false)

  const isDark = theme === 'dark'
  const isSepia = theme === 'sepia'
  const tokens = {
    panel: isDark ? 'bg-slate-800' : isSepia ? 'bg-amber-50' : 'bg-white',
    border: isDark ? 'border-slate-700' : isSepia ? 'border-amber-200' : 'border-stone-200',
    primary: isDark ? 'text-slate-100' : isSepia ? 'text-amber-950' : 'text-gray-900',
    secondary: isDark ? 'text-slate-400' : isSepia ? 'text-amber-800' : 'text-gray-500',
    muted: isDark ? 'text-slate-500' : isSepia ? 'text-amber-700/70' : 'text-gray-400',
    input: isDark
      ? 'bg-slate-900 border-slate-600 text-slate-100 placeholder-slate-500'
      : isSepia
        ? 'bg-amber-100/50 border-amber-300 text-amber-950 placeholder-amber-700/50'
        : 'bg-white border-stone-300 text-gray-900 placeholder-gray-400',
    dropdown: isDark
      ? 'bg-slate-900 border-slate-600 text-slate-100'
      : isSepia ? 'bg-amber-50 border-amber-300 text-amber-950'
        : 'bg-white border-stone-300 text-gray-900',
    optionActive: isDark ? 'bg-slate-700' : isSepia ? 'bg-amber-200/60' : 'bg-stone-100',
    pill: isDark
      ? 'border-slate-600 text-slate-300 hover:bg-slate-700/60'
      : isSepia ? 'border-amber-300 text-amber-900 hover:bg-amber-100'
        : 'border-stone-300 text-gray-600 hover:bg-stone-100',
    pillOn: 'bg-indigo-600 border-indigo-600 text-white hover:bg-indigo-600',
    hover: isDark ? 'hover:bg-slate-700/60' : isSepia ? 'hover:bg-amber-100' : 'hover:bg-stone-100',
  }
  const turnstileTheme = isDark ? 'dark' : isSepia ? 'light' : 'auto'

  const { ref: tsRef, token: tsToken, reset: tsReset } = useTurnstile(
    interacted ? siteKey : '', turnstileTheme,
  )

  // Re-seed when the host reopens the modal with a different chapter or a
  // freshly highlighted passage.
  useEffect(() => {
    if (!open) return
    setChapterNumber(defaultChapter)
    setQuote(initialQuote)
    setError('')
    setSuccess(false)
  }, [open, defaultChapter, initialQuote])

  // Defer the site-key fetch and the widget until the reader actually starts
  // filling the form in — most people open it, read it, and close it.
  useEffect(() => {
    if (!interacted) return
    fetch('/api/public/turnstile-site-key', { credentials: 'same-origin' })
      .then(r => r.json())
      .then(data => setSiteKey(data.site_key || ''))
      .catch(() => {})
  }, [interacted])

  if (!open) return null

  const markInteracted = () => { if (!interacted) setInteracted(true) }

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError('')
    if (!reportType) return setError('Please pick what kind of problem this is.')
    if (!problem.trim()) return setError('Please describe the problem.')
    if (email.trim() && !email.includes('@')) return setError('That email address doesn\'t look valid.')
    if (siteKey && !tsToken) return setError('Please complete the CAPTCHA verification.')

    setSubmitting(true)
    try {
      await publicApi.submitErrorReport({
        book_id: Number(bookId),
        chapter_number: chapterNumber,
        report_type: reportType,
        quote: quote.trim().slice(0, QUOTE_MAX) || null,
        problem: problem.trim(),
        suggested_fix: suggestedFix.trim() || null,
        reporter_email: email.trim() || null,
        turnstile_token: tsToken || '',
      })
      setSuccess(true)
      setTimeout(onClose, 2500)
    } catch (err) {
      setError(err.detail || err.message || 'Could not submit the report.')
      tsReset()
    } finally {
      setSubmitting(false)
    }
  }

  const labelClass = `block text-xs font-medium mb-1 ${tokens.secondary}`

  return (
    <>
      <div className="fixed inset-0 z-40 bg-black/50" onClick={onClose} />
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Report an error"
        className={`fixed top-12 left-1/2 -translate-x-1/2 z-50 w-[560px] max-w-[92vw] ${tokens.panel}
          border ${tokens.border} rounded-xl shadow-2xl flex flex-col max-h-[86vh]`}
      >
        <div className={`flex items-center justify-between px-4 py-3 border-b ${tokens.border}`}>
          <div className="min-w-0">
            <h2 className={`text-sm font-semibold ${tokens.primary}`}>Report an error</h2>
            {bookTitle && (
              <p className={`text-xs truncate ${tokens.muted}`}>{bookTitle}</p>
            )}
          </div>
          <button onClick={onClose} aria-label="Close"
                  className={`p-1 rounded ${tokens.secondary} ${tokens.hover}`}>
            <X size={18} />
          </button>
        </div>

        {success ? (
          <div className="px-6 py-10 text-center">
            <CheckCircle size={40} className="mx-auto text-emerald-500" />
            <p className={`mt-3 text-sm font-medium ${tokens.primary}`}>Thank you!</p>
            <p className={`mt-1 text-xs ${tokens.secondary}`}>
              Your report has been sent to the editor.
            </p>
          </div>
        ) : (
          <form onSubmit={handleSubmit} onChange={markInteracted}
                className="px-4 py-3 space-y-3 overflow-y-auto">
            <div>
              <label htmlFor="report-chapter" className={labelClass}>Where is it?</label>
              <ChapterPicker
                inputId="report-chapter"
                value={chapterNumber}
                onChange={setChapterNumber}
                chapters={chapters}
                tokens={tokens}
              />
            </div>

            <div>
              <span className={labelClass}>What kind of problem?</span>
              <div className="flex flex-wrap gap-1.5">
                {REPORT_TYPES.map(t => (
                  <button
                    key={t.id}
                    type="button"
                    onClick={() => { setReportType(t.id); markInteracted() }}
                    className={`px-2.5 py-1 rounded-full border text-xs transition-colors ${
                      reportType === t.id ? tokens.pillOn : tokens.pill
                    }`}
                  >
                    {t.label}
                  </button>
                ))}
              </div>
            </div>

            <div>
              <label htmlFor="report-quote" className={labelClass}>
                The text with the problem <span className={tokens.muted}>(optional)</span>
              </label>
              <textarea
                id="report-quote"
                value={quote}
                onChange={(e) => setQuote(e.target.value.slice(0, QUOTE_MAX))}
                rows={2}
                placeholder="Paste or highlight the exact wording — it's how we find the passage."
                className={`w-full rounded-lg border px-3 py-2 text-sm resize-y ${tokens.input}`}
              />
              {quote && (
                <div className="flex items-center justify-between mt-1">
                  <button type="button" onClick={() => setQuote('')}
                          className={`text-xs underline ${tokens.muted}`}>
                    clear
                  </button>
                  <span className={`text-xs ${tokens.muted}`}>{quote.length}/{QUOTE_MAX}</span>
                </div>
              )}
            </div>

            <div>
              <label htmlFor="report-problem" className={labelClass}>
                What&apos;s wrong? <span className="text-rose-500">*</span>
              </label>
              <textarea
                id="report-problem"
                value={problem}
                onChange={(e) => setProblem(e.target.value.slice(0, TEXT_MAX))}
                rows={3}
                required
                placeholder="e.g. This character's name is spelled two different ways in this chapter."
                className={`w-full rounded-lg border px-3 py-2 text-sm resize-y ${tokens.input}`}
              />
            </div>

            <div>
              <label htmlFor="report-fix" className={labelClass}>
                Suggested fix <span className={tokens.muted}>(optional)</span>
              </label>
              <textarea
                id="report-fix"
                value={suggestedFix}
                onChange={(e) => setSuggestedFix(e.target.value.slice(0, TEXT_MAX))}
                rows={2}
                placeholder="What should it say instead?"
                className={`w-full rounded-lg border px-3 py-2 text-sm resize-y ${tokens.input}`}
              />
            </div>

            <div>
              <label htmlFor="report-email" className={labelClass}>
                Your email <span className={tokens.muted}>(optional — only used to follow up)</span>
              </label>
              <input
                id="report-email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@example.com"
                className={`w-full rounded-lg border px-3 py-2 text-sm ${tokens.input}`}
              />
            </div>

            {siteKey && (
              <div className="flex justify-center">
                <div ref={tsRef} />
              </div>
            )}

            {error && (
              <p className="text-center text-xs text-rose-500">{error}</p>
            )}

            <button
              type="submit"
              disabled={submitting}
              className="w-full flex items-center justify-center gap-2 rounded-lg bg-indigo-600
                         px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500
                         disabled:opacity-60 disabled:cursor-not-allowed transition-colors"
            >
              {submitting
                ? (<><Loader2 size={15} className="animate-spin" /> Sending…</>)
                : (<><Send size={15} /> Send report</>)}
            </button>
          </form>
        )}
      </div>
    </>
  )
}
