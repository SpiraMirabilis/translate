import { useCallback, useEffect, useState } from 'react'
import { X, MessageCircle, Loader2 } from 'lucide-react'
import CommentForm, { loadIdentity } from './CommentForm'
import CommentItem from './CommentItem'
import CommentTree from './CommentTree'

async function commentRequest(method, path, body, uuid, opts = {}) {
  const headers = { 'Content-Type': 'application/json' }
  if (uuid) headers['X-Commenter-UUID'] = uuid
  const res = await fetch(path, {
    method,
    credentials: 'same-origin',
    headers,
    body: body ? JSON.stringify(body) : undefined,
    cache: opts.cache,           // 'no-store' to bypass the 60s browser cache
  })
  if (!res.ok) {
    let detail
    try { detail = (await res.json()).detail } catch { detail = res.statusText }
    throw new Error(detail || `HTTP ${res.status}`)
  }
  return res.json()
}

export default function ReaderComments({ open, onClose, bookId, chapterNumber, theme, themeMode = 'light' }) {
  const [comments, setComments] = useState([])
  const [enabled, setEnabled] = useState(true)
  const [viewer, setViewer] = useState({ is_trusted: false, captcha_required: true })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [replyTo, setReplyTo] = useState(null)
  const [postedThisSession, setPostedThisSession] = useState(false)
  const identity = loadIdentity()
  const ownUuid = identity?.uuid || null

  const load = useCallback(async ({ fresh = false } = {}) => {
    if (!bookId || chapterNumber == null) return
    setLoading(true)
    setError('')
    try {
      const data = await commentRequest(
        'GET',
        `/api/public/comments/chapter/${bookId}/${chapterNumber}`,
        null,
        ownUuid,
        fresh ? { cache: 'no-store' } : undefined,
      )
      setComments(data.comments || [])
      setEnabled(data.enabled !== false)
      if (data.viewer) setViewer(data.viewer)
    } catch (e) {
      setError(e.message || 'Failed to load comments.')
    } finally {
      setLoading(false)
    }
  }, [bookId, chapterNumber, ownUuid])

  useEffect(() => {
    if (open) load()
  }, [open, load])

  const handleSubmitted = useCallback(() => {
    setReplyTo(null)
    setPostedThisSession(true)
    load({ fresh: true })
  }, [load])

  const captchaRequired = viewer.captcha_required && !postedThisSession

  const handleEdit = useCallback(async (id, body) => {
    // Optimistic update so the edit reflects immediately; load() confirms.
    const editedAt = new Date().toISOString()
    setComments(prev => prev.map(c => (c.id === id ? { ...c, body, edited_at: editedAt } : c)))
    try {
      await commentRequest('PUT', `/api/public/comments/${id}`, { body }, ownUuid)
    } finally {
      await load({ fresh: true })
    }
  }, [load, ownUuid])

  const handleDelete = useCallback(async (id) => {
    // Optimistic: mark as removed locally so the user sees instant feedback.
    const deletedAt = new Date().toISOString()
    setComments(prev => prev.map(c => (
      c.id === id ? { ...c, status: 'deleted', body: '[removed]', deleted_at: deletedAt } : c
    )))
    try {
      await commentRequest('DELETE', `/api/public/comments/${id}`, null, ownUuid)
    } finally {
      await load({ fresh: true })
    }
  }, [load, ownUuid])

  if (!open) return null

  const isDark = themeMode === 'dark'
  const isSepia = themeMode === 'sepia'
  const panelBg = isDark ? 'bg-slate-800' : isSepia ? 'bg-amber-50' : 'bg-white'
  const borderColor = isDark ? 'border-slate-700' : isSepia ? 'border-amber-200' : 'border-stone-200'
  const textPrimary = isDark ? 'text-slate-100' : 'text-gray-900'
  const textSecondary = isDark ? 'text-slate-400' : 'text-gray-500'

  // Theme bundle for child components
  const childTheme = {
    cardBg: isDark ? 'bg-slate-900/40' : isSepia ? 'bg-amber-100/40' : 'bg-stone-50',
    cardBorder: borderColor,
    nameText: textPrimary,
    subtleText: textSecondary,
    bodyText: isDark ? 'text-slate-200' : 'text-gray-800',
    inputBg: isDark ? 'bg-slate-900' : isSepia ? 'bg-amber-50' : 'bg-white',
    inputBorder: borderColor,
    inputText: textPrimary,
    labelText: textSecondary,
    formBg: isDark ? 'bg-slate-900/50' : isSepia ? 'bg-amber-100/40' : 'bg-stone-50',
    threadBorder: borderColor,
    modalText: textPrimary,
  }

  return (
    <>
      <div className="fixed inset-0 z-40 bg-black/40" onClick={onClose} />
      <div className={`fixed inset-x-0 bottom-0 z-50 ${panelBg} border-t ${borderColor} shadow-2xl rounded-t-xl flex flex-col h-[75vh] max-h-[85vh]`}>
        {/* Header */}
        <div className={`px-4 py-3 border-b ${borderColor} flex items-center justify-between shrink-0`}>
          <div className="flex items-center gap-2">
            <MessageCircle size={18} className={textPrimary} />
            <h2 className={`font-semibold ${textPrimary}`}>
              Comments {comments.length > 0 && <span className={`text-xs font-normal ${textSecondary}`}>({comments.filter(c => c.status !== 'deleted').length})</span>}
            </h2>
          </div>
          <button onClick={onClose} className={`${textSecondary} hover:${textPrimary} p-1`}>
            <X size={18} />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-4 py-3">
          <div className="max-w-2xl mx-auto space-y-4">
            {!enabled ? (
              <p className={`text-sm ${textSecondary} text-center py-12`}>
                Comments are disabled for this book.
              </p>
            ) : (
              <>
                {/* Top-level form (only when not actively replying) */}
                {!replyTo && (
                  <CommentForm
                    bookId={bookId}
                    chapterNumber={chapterNumber}
                    onSubmitted={handleSubmitted}
                    theme={childTheme}
                    themeMode={themeMode}
                    captchaRequired={captchaRequired}
                  />
                )}

                {/* List */}
                {loading && (
                  <div className="flex justify-center py-6">
                    <Loader2 size={20} className={`${textSecondary} animate-spin`} />
                  </div>
                )}

                {error && (
                  <p className="text-sm text-rose-500 text-center py-3">{error}</p>
                )}

                {!loading && !error && comments.length === 0 && (
                  <p className={`text-sm ${textSecondary} text-center py-6`}>
                    Be the first to comment.
                  </p>
                )}

                {!loading && comments.length > 0 && (
                  <CommentTree
                    comments={comments}
                    ownUuid={ownUuid}
                    theme={childTheme}
                    onReply={(c) => setReplyTo(c)}
                    onEdit={handleEdit}
                    onDelete={handleDelete}
                  />
                )}

                {/* Reply form anchored at bottom when active */}
                {replyTo && (
                  <div className="sticky bottom-0 pt-3">
                    <p className={`text-xs ${textSecondary} mb-1.5`}>
                      Replying to <span className={textPrimary}>{replyTo.display_name}</span>
                    </p>
                    <CommentForm
                      parentId={replyTo.id}
                      bookId={bookId}
                      chapterNumber={chapterNumber}
                      onSubmitted={handleSubmitted}
                      onCancel={() => setReplyTo(null)}
                      theme={childTheme}
                      themeMode={themeMode}
                      captchaRequired={captchaRequired}
                    />
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      </div>
    </>
  )
}
