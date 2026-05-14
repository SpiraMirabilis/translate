import { useState } from 'react'
import { CornerDownRight, Edit2, Trash2, Loader2, Check, X } from 'lucide-react'
import MarkdownView from './MarkdownView'

function relTime(iso) {
  if (!iso) return ''
  const then = new Date(iso).getTime()
  if (!then) return ''
  const diff = (Date.now() - then) / 1000
  if (diff < 60) return 'just now'
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`
  return new Date(iso).toLocaleDateString()
}

export default function CommentItem({
  comment,
  ownUuid,
  onReply,
  onEdit,
  onDelete,
  theme = {},
  level = 0,
}) {
  const [editing, setEditing] = useState(false)
  const [editBody, setEditBody] = useState(comment.body || '')
  const [busy, setBusy] = useState(false)

  const isOwn = !!comment.is_own || (ownUuid && comment.commenter_uuid === ownUuid)
  const isDeleted = comment.status === 'deleted'

  const handleEditSave = async () => {
    if (!editBody.trim()) return
    setBusy(true)
    try {
      await onEdit(comment.id, editBody.trim())
      setEditing(false)
    } finally {
      setBusy(false)
    }
  }

  const handleDelete = async () => {
    if (!confirm('Delete this comment?')) return
    setBusy(true)
    try { await onDelete(comment.id) } finally { setBusy(false) }
  }

  const cardBg = theme.cardBg || 'bg-white'
  const cardBorder = theme.cardBorder || 'border-stone-200'
  const nameCls = theme.nameText || 'text-gray-900'
  const timeCls = theme.subtleText || 'text-gray-500'
  const bodyCls = theme.bodyText || 'text-gray-800'

  return (
    <div className={`rounded-lg border ${cardBorder} ${cardBg} p-3`}>
      <div className="flex items-baseline gap-2 flex-wrap">
        <span className={`font-medium text-sm ${nameCls}`}>
          {comment.display_name || 'Anonymous'}
          {!isDeleted && comment.fingerprint && (
            <span className={`ml-1 text-[11px] font-normal font-mono opacity-60 ${timeCls}`}>
              ({comment.fingerprint})
            </span>
          )}
        </span>
        <span className={`text-xs ${timeCls}`}>{relTime(comment.created_at)}</span>
        {comment.edited_at && !isDeleted && (
          <span className={`text-xs italic ${timeCls}`}>(edited)</span>
        )}
      </div>

      <div className="mt-1.5">
        {editing ? (
          <div className="space-y-2">
            <textarea
              value={editBody}
              onChange={e => setEditBody(e.target.value)}
              rows={4}
              maxLength={4000}
              className={`w-full px-2 py-1.5 rounded border text-sm ${theme.inputBorder || 'border-gray-300'} ${theme.inputBg || 'bg-white'} ${theme.inputText || 'text-gray-900'}`}
            />
            <div className="flex gap-1.5 justify-end">
              <button
                onClick={() => { setEditing(false); setEditBody(comment.body || '') }}
                className={`px-2 py-1 rounded text-xs ${timeCls} hover:${nameCls}`}
                disabled={busy}
              >
                <X size={12} className="inline -mt-0.5" /> Cancel
              </button>
              <button
                onClick={handleEditSave}
                disabled={busy || !editBody.trim()}
                className="px-3 py-1 rounded text-xs bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-50"
              >
                {busy ? <Loader2 size={12} className="inline animate-spin" /> : <Check size={12} className="inline -mt-0.5" />} Save
              </button>
            </div>
          </div>
        ) : (
          <MarkdownView
            source={isDeleted ? '*[removed]*' : comment.body}
            className={`text-sm leading-relaxed ${bodyCls} ${isDeleted ? 'italic opacity-60' : ''}`}
          />
        )}
      </div>

      {!isDeleted && !editing && (
        <div className="mt-2 flex gap-3 text-xs items-center">
          {onReply && comment.depth < 5 && (
            <button onClick={() => onReply(comment)} className={`${timeCls} hover:${nameCls} inline-flex items-center gap-1`}>
              <CornerDownRight size={12} /> Reply
            </button>
          )}
          {isOwn && (
            <>
              <button onClick={() => setEditing(true)} className={`${timeCls} hover:${nameCls} inline-flex items-center gap-1`}>
                <Edit2 size={12} /> Edit
              </button>
              <button onClick={handleDelete} disabled={busy} className={`${timeCls} hover:text-rose-500 inline-flex items-center gap-1 disabled:opacity-50`}>
                <Trash2 size={12} /> Delete
              </button>
            </>
          )}
        </div>
      )}
    </div>
  )
}
