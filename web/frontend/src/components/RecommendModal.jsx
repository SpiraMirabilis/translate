import { useState, useEffect, useRef, useCallback } from 'react'
import { X, Loader2, Send, CheckCircle } from 'lucide-react'

const LANGUAGES = [
  { value: 'zh', label: 'Chinese' },
  { value: 'ko', label: 'Korean' },
  { value: 'ja', label: 'Japanese' },
]

export default function RecommendModal({ onClose, theme }) {
  const t = theme
  const [form, setForm] = useState({
    novel_title: '',
    author: '',
    source_url: '',
    source_language: 'zh',
    description: '',
    requester_name: '',
    requester_email: '',
    notes: '',
  })
  const [siteKey, setSiteKey] = useState('')
  const [turnstileToken, setTurnstileToken] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState(false)
  const turnstileRef = useRef(null)
  const widgetIdRef = useRef(null)

  // Fetch the Turnstile site key
  useEffect(() => {
    fetch('/api/public/turnstile-site-key', { credentials: 'same-origin' })
      .then(r => r.json())
      .then(data => setSiteKey(data.site_key || ''))
      .catch(() => {})
  }, [])

  // Render Turnstile widget when site key is available
  const renderTurnstile = useCallback(() => {
    if (!siteKey || !turnstileRef.current || widgetIdRef.current != null) return
    if (!window.turnstile) return
    widgetIdRef.current = window.turnstile.render(turnstileRef.current, {
      sitekey: siteKey,
      callback: (token) => setTurnstileToken(token),
      'expired-callback': () => setTurnstileToken(''),
      theme: t?.turnstileTheme || 'auto',
    })
  }, [siteKey, t])

  useEffect(() => {
    // Load the Turnstile script if not already loaded
    if (document.querySelector('script[src*="turnstile"]')) {
      // Script already loaded, render directly or wait for it
      if (window.turnstile) {
        renderTurnstile()
      } else {
        const existing = document.querySelector('script[src*="turnstile"]')
        existing.addEventListener('load', renderTurnstile)
        return () => existing.removeEventListener('load', renderTurnstile)
      }
      return
    }
    const script = document.createElement('script')
    script.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit'
    script.async = true
    script.onload = renderTurnstile
    document.head.appendChild(script)
  }, [renderTurnstile])

  // Cleanup turnstile widget on unmount
  useEffect(() => {
    return () => {
      if (widgetIdRef.current != null && window.turnstile) {
        try { window.turnstile.remove(widgetIdRef.current) } catch {}
        widgetIdRef.current = null
      }
    }
  }, [])

  const update = (field) => (e) => setForm(f => ({ ...f, [field]: e.target.value }))

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError('')

    if (!form.novel_title.trim()) return setError('Novel title is required.')
    if (!form.source_url.trim()) return setError('Source URL is required.')
    if (!form.requester_name.trim()) return setError('Your name is required.')
    if (!form.requester_email.trim() || !form.requester_email.includes('@'))
      return setError('A valid email is required.')
    if (!turnstileToken && siteKey) return setError('Please complete the CAPTCHA verification.')

    setSubmitting(true)
    try {
      const res = await fetch('/api/public/recommendations', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...form, turnstile_token: turnstileToken }),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.detail || `Error ${res.status}`)
      }
      setSuccess(true)
      setTimeout(onClose, 2500)
    } catch (err) {
      setError(err.message || 'Submission failed. Please try again.')
      // Reset turnstile on failure
      if (widgetIdRef.current != null && window.turnstile) {
        window.turnstile.reset(widgetIdRef.current)
        setTurnstileToken('')
      }
    } finally {
      setSubmitting(false)
    }
  }

  // Theme-aware colors for the modal
  const modalBg = t?.modalBg || 'bg-white'
  const modalText = t?.modalText || 'text-gray-900'
  const modalBorder = t?.modalBorder || 'border-gray-200'
  const inputBg = t?.inputBg || 'bg-gray-50'
  const inputBorder = t?.inputBorder || 'border-gray-300'
  const inputText = t?.inputText || 'text-gray-900'
  const labelText = t?.labelText || 'text-gray-700'
  const subtleText = t?.subtleText || 'text-gray-500'

  if (success) {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
        <div className={`${modalBg} rounded-xl p-8 shadow-2xl max-w-sm w-full text-center`} onClick={e => e.stopPropagation()}>
          <CheckCircle size={48} className="mx-auto mb-4 text-emerald-500" />
          <h3 className={`text-xl font-bold ${modalText} mb-2`}>Thank you!</h3>
          <p className={subtleText}>Your recommendation has been submitted. We'll review it soon.</p>
        </div>
      </div>
    )
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div
        className={`${modalBg} rounded-xl shadow-2xl max-w-lg w-full max-h-[90vh] overflow-y-auto`}
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div className={`flex items-center justify-between px-6 py-4 border-b ${modalBorder}`}>
          <h2 className={`text-lg font-bold ${modalText}`}>Recommend a Novel</h2>
          <button onClick={onClose} className={`${subtleText} hover:${modalText} transition-colors`}>
            <X size={20} />
          </button>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="px-6 py-4 space-y-4">
          {/* Novel Title */}
          <div>
            <label className={`block text-sm font-medium ${labelText} mb-1`}>
              Novel Title <span className="text-rose-500">*</span>
            </label>
            <input
              type="text"
              value={form.novel_title}
              onChange={update('novel_title')}
              placeholder="e.g. Reverend Insanity"
              className={`w-full px-3 py-2 rounded-lg border ${inputBorder} ${inputBg} ${inputText} focus:outline-none focus:ring-2 focus:ring-indigo-500`}
            />
          </div>

          {/* Author */}
          <div>
            <label className={`block text-sm font-medium ${labelText} mb-1`}>Author</label>
            <input
              type="text"
              value={form.author}
              onChange={update('author')}
              placeholder="Author name (if known)"
              className={`w-full px-3 py-2 rounded-lg border ${inputBorder} ${inputBg} ${inputText} focus:outline-none focus:ring-2 focus:ring-indigo-500`}
            />
          </div>

          {/* Source URL */}
          <div>
            <label className={`block text-sm font-medium ${labelText} mb-1`}>
              Source URL <span className="text-rose-500">*</span>
            </label>
            <input
              type="url"
              value={form.source_url}
              onChange={update('source_url')}
              placeholder="Link to raw chapters"
              className={`w-full px-3 py-2 rounded-lg border ${inputBorder} ${inputBg} ${inputText} focus:outline-none focus:ring-2 focus:ring-indigo-500`}
            />
            <p className={`text-xs ${subtleText} mt-1`}>Where can we find the untranslated chapters?</p>
          </div>

          {/* Source Language */}
          <div>
            <label className={`block text-sm font-medium ${labelText} mb-1`}>Source Language</label>
            <select
              value={form.source_language}
              onChange={update('source_language')}
              className={`w-full px-3 py-2 rounded-lg border ${inputBorder} ${inputBg} ${inputText} focus:outline-none focus:ring-2 focus:ring-indigo-500`}
            >
              {LANGUAGES.map(l => (
                <option key={l.value} value={l.value}>{l.label}</option>
              ))}
            </select>
          </div>

          {/* Description */}
          <div>
            <label className={`block text-sm font-medium ${labelText} mb-1`}>Description</label>
            <textarea
              value={form.description}
              onChange={update('description')}
              placeholder="Brief description of the novel (genre, synopsis, etc.)"
              rows={2}
              className={`w-full px-3 py-2 rounded-lg border ${inputBorder} ${inputBg} ${inputText} focus:outline-none focus:ring-2 focus:ring-indigo-500 resize-none`}
            />
          </div>

          <hr className={`${modalBorder}`} />

          {/* Requester Name */}
          <div>
            <label className={`block text-sm font-medium ${labelText} mb-1`}>
              Your Name <span className="text-rose-500">*</span>
            </label>
            <input
              type="text"
              value={form.requester_name}
              onChange={update('requester_name')}
              className={`w-full px-3 py-2 rounded-lg border ${inputBorder} ${inputBg} ${inputText} focus:outline-none focus:ring-2 focus:ring-indigo-500`}
            />
          </div>

          {/* Requester Email */}
          <div>
            <label className={`block text-sm font-medium ${labelText} mb-1`}>
              Your Email <span className="text-rose-500">*</span>
            </label>
            <input
              type="email"
              value={form.requester_email}
              onChange={update('requester_email')}
              className={`w-full px-3 py-2 rounded-lg border ${inputBorder} ${inputBg} ${inputText} focus:outline-none focus:ring-2 focus:ring-indigo-500`}
            />
          </div>

          {/* Notes */}
          <div>
            <label className={`block text-sm font-medium ${labelText} mb-1`}>Notes / Comments</label>
            <textarea
              value={form.notes}
              onChange={update('notes')}
              placeholder="Any additional context or comments"
              rows={2}
              className={`w-full px-3 py-2 rounded-lg border ${inputBorder} ${inputBg} ${inputText} focus:outline-none focus:ring-2 focus:ring-indigo-500 resize-none`}
            />
          </div>

          {/* Turnstile */}
          {siteKey && (
            <div className="flex justify-center">
              <div ref={turnstileRef} />
            </div>
          )}

          {/* Error */}
          {error && (
            <p className="text-sm text-rose-500 text-center">{error}</p>
          )}

          {/* Submit */}
          <button
            type="submit"
            disabled={submitting}
            className="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg bg-indigo-600 text-white font-medium hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {submitting ? (
              <><Loader2 size={18} className="animate-spin" /> Submitting...</>
            ) : (
              <><Send size={18} /> Submit Recommendation</>
            )}
          </button>
        </form>
      </div>
    </div>
  )
}
