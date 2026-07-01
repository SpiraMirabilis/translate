import { useEffect, useRef, useState } from 'react'
import { Send, Loader2, X } from 'lucide-react'
import { useTurnstile } from '../hooks/useTurnstile'

const STORAGE_KEY = 't9-commenter-identity'

function loadIdentity() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw)
    if (parsed && typeof parsed === 'object' && parsed.uuid) return parsed
  } catch { /* ignore */ }
  return null
}

function saveIdentity(identity) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(identity))
  } catch { /* ignore */ }
}

function makeUuid() {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) {
    return crypto.randomUUID()
  }
  // Fallback (RFC 4122 v4) for environments without crypto.randomUUID
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
    const r = (Math.random() * 16) | 0
    const v = c === 'x' ? r : (r & 0x3) | 0x8
    return v.toString(16)
  })
}

/**
 * Comment submission form. Used both for top-level posts and replies.
 *
 * Props:
 *   isPublic     - whether the host page is the public reader
 *   parentId     - if set, this is a reply form
 *   bookId, chapterNumber - target chapter
 *   onSubmitted  - called after successful submit with the new comment
 *   onCancel     - called when the user cancels (replies only)
 *   themeMode    - 'light' | 'sepia' | 'dark' for Turnstile widget theming
 *   theme        - object of Tailwind class strings for surfaces, inputs, etc.
 */
export default function CommentForm({
  parentId = null,
  bookId,
  chapterNumber,
  onSubmitted,
  onCancel,
  themeMode = 'light',
  theme = {},
  captchaRequired = true,
}) {
  const initial = loadIdentity()
  const [displayName, setDisplayName] = useState(initial?.displayName || '')
  const [email, setEmail] = useState(initial?.email || '')
  const [body, setBody] = useState('')
  const [notifyReplies, setNotifyReplies] = useState(!!initial?.notifyReplies)
  const [siteKey, setSiteKey] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  // Defer Turnstile script + widget until the user actually starts filling
  // out the form. Most readers open the drawer just to read; rendering the
  // widget for them wastes a network request and adds visual noise.
  const [interacted, setInteracted] = useState(false)
  const formRef = useRef(null)

  const markInteracted = () => { if (!interacted) setInteracted(true) }

  const turnstileTheme = themeMode === 'dark' ? 'dark' : themeMode === 'sepia' ? 'light' : 'auto'
  // useTurnstile is a no-op when siteKey is '' — passing an empty string
  // both prevents the script load and keeps the hook's internal state idle.
  const { ref: tsRef, token: tsToken, reset: tsReset } = useTurnstile(
    captchaRequired && interacted ? siteKey : '',
    turnstileTheme,
  )

  useEffect(() => {
    if (!captchaRequired || !interacted) return
    fetch('/api/public/turnstile-site-key', { credentials: 'same-origin' })
      .then(r => r.json())
      .then(data => setSiteKey(data.site_key || ''))
      .catch(() => {})
  }, [captchaRequired, interacted])

  const validate = () => {
    if (!displayName.trim()) return 'Please enter a display name.'
    if (displayName.trim().length > 40) return 'Display name must be 40 characters or less.'
    if (!email.trim() || !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email.trim()))
      return 'Please enter a valid email address.'
    if (!body.trim()) return 'Comment cannot be empty.'
    if (body.length > 4000) return 'Comment must be 4000 characters or less.'
    if (captchaRequired && siteKey && !tsToken) return 'Please complete the CAPTCHA verification.'
    return ''
  }

  const handleSubmit = async (e) => {
    e.preventDefault()
    const err = validate()
    if (err) return setError(err)
    setError('')

    let identity = loadIdentity()
    if (!identity?.uuid) {
      identity = { uuid: makeUuid(), displayName: displayName.trim(), email: email.trim(), notifyReplies }
      saveIdentity(identity)
    } else {
      // Refresh display name/email/preference on each submit
      identity = { ...identity, displayName: displayName.trim(), email: email.trim(), notifyReplies }
      saveIdentity(identity)
    }

    setSubmitting(true)
    try {
      const res = await fetch('/api/public/comments', {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'Content-Type': 'application/json',
          'X-Commenter-UUID': identity.uuid,
        },
        body: JSON.stringify({
          book_id: bookId,
          chapter_number: chapterNumber,
          parent_id: parentId,
          commenter_uuid: identity.uuid,
          display_name: identity.displayName,
          email: identity.email,
          body: body.trim(),
          turnstile_token: tsToken || '',
          notify_replies: notifyReplies,
        }),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.detail || `HTTP ${res.status}`)
      }
      const data = await res.json()
      setBody('')
      tsReset()
      onSubmitted && onSubmitted(data.comment)
    } catch (err2) {
      setError(err2.message || 'Submission failed.')
      tsReset()
    } finally {
      setSubmitting(false)
    }
  }

  const inputCls = `w-full px-3 py-2 rounded-lg border ${theme.inputBorder || 'border-gray-300'} ${theme.inputBg || 'bg-white'} ${theme.inputText || 'text-gray-900'} text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500`
  const labelCls = `block text-xs font-medium ${theme.labelText || 'text-gray-600'} mb-1`

  return (
    <form ref={formRef} onSubmit={handleSubmit} className={`p-3 rounded-lg ${theme.formBg || 'bg-stone-50'} space-y-2.5`}>
      <div className="flex gap-2">
        <div className="flex-1">
          <label className={labelCls}>Display name</label>
          <input
            type="text"
            value={displayName}
            onChange={e => { setDisplayName(e.target.value); markInteracted() }}
            maxLength={40}
            placeholder="Your name"
            className={inputCls}
          />
        </div>
        <div className="flex-1">
          <label className={labelCls}>Email <span className={theme.subtleText || 'text-gray-400'}>(not shown publicly)</span></label>
          <input
            type="email"
            value={email}
            onChange={e => { setEmail(e.target.value); markInteracted() }}
            maxLength={255}
            placeholder="you@example.com"
            className={inputCls}
          />
        </div>
      </div>
      <div>
        <label className={labelCls}>
          {parentId ? 'Reply' : 'Comment'}
          <span className={`float-right ${body.length > 3800 ? 'text-rose-500' : (theme.subtleText || 'text-gray-400')}`}>
            {body.length}/4000
          </span>
        </label>
        <textarea
          value={body}
          onChange={e => { setBody(e.target.value); markInteracted() }}
          onFocus={markInteracted}
          rows={parentId ? 3 : 4}
          maxLength={4000}
          placeholder="Markdown supported: **bold**, *italic*, `code`, [link](url)"
          className={`${inputCls} resize-none`}
        />
      </div>
      <label className="flex items-center gap-2 text-xs cursor-pointer select-none">
        <input
          type="checkbox"
          checked={notifyReplies}
          onChange={e => { setNotifyReplies(e.target.checked); markInteracted() }}
          className="h-3.5 w-3.5 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500 focus:ring-offset-0"
        />
        <span className={theme.subtleText || 'text-gray-500'}>
          Email me when someone replies
        </span>
      </label>
      {captchaRequired && interacted && siteKey && (
        <div className="flex justify-center">
          <div ref={tsRef} />
        </div>
      )}
      {error && <p className="text-sm text-rose-500">{error}</p>}
      <div className="flex items-center gap-2 justify-end">
        {onCancel && (
          <button type="button" onClick={onCancel} className={`px-3 py-1.5 rounded-lg text-sm ${theme.subtleText || 'text-gray-500'} hover:${theme.modalText || 'text-gray-900'}`}>
            <X size={14} className="inline -mt-0.5 mr-1" /> Cancel
          </button>
        )}
        <button
          type="submit"
          disabled={submitting}
          className="px-4 py-1.5 rounded-lg bg-indigo-600 text-white text-sm font-medium hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed inline-flex items-center gap-1.5"
        >
          {submitting ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
          {parentId ? 'Reply' : 'Post comment'}
        </button>
      </div>
    </form>
  )
}

export { STORAGE_KEY as IDENTITY_KEY, loadIdentity }
