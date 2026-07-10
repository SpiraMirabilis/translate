import CommentItem from './CommentItem'

const MAX_VISUAL_DEPTH = 5

function buildTree(flat) {
  // Replies whose parent isn't in the visible set (e.g. the parent was edited
  // and went back to pending) are promoted to roots — otherwise the whole
  // subtree silently disappears while the count badge still includes it.
  const visible = new Set(flat.map(c => c.id))
  const byParent = new Map()
  byParent.set(null, [])
  for (const c of flat) {
    let pid = c.parent_id || null
    if (pid !== null && !visible.has(pid)) pid = null
    if (!byParent.has(pid)) byParent.set(pid, [])
    byParent.get(pid).push(c)
  }
  return byParent
}

function renderNode(node, byParent, ownUuid, theme, callbacks, level) {
  const children = byParent.get(node.id) || []
  const indentLevel = Math.min(level, MAX_VISUAL_DEPTH)
  const indentClass = [
    '',
    'ml-3 sm:ml-6 pl-2 border-l',
    'ml-3 sm:ml-6 pl-2 border-l',
    'ml-3 sm:ml-6 pl-2 border-l',
    'ml-3 sm:ml-6 pl-2 border-l',
    'ml-3 sm:ml-6 pl-2 border-l',
  ][indentLevel]

  return (
    <div key={node.id} className={`${level > 0 ? `${indentClass} ${theme.threadBorder || 'border-stone-200'}` : ''}`}>
      <CommentItem
        comment={node}
        ownUuid={ownUuid}
        theme={theme}
        level={level}
        onReply={callbacks.onReply}
        onEdit={callbacks.onEdit}
        onDelete={callbacks.onDelete}
      />
      {children.length > 0 && (
        <div className="mt-2 space-y-2">
          {children.map(child => renderNode(child, byParent, ownUuid, theme, callbacks, level + 1))}
        </div>
      )}
    </div>
  )
}

export default function CommentTree({ comments, ownUuid, theme, onReply, onEdit, onDelete }) {
  const byParent = buildTree(comments || [])
  const roots = byParent.get(null) || []
  if (!roots.length) return null

  return (
    <div className="space-y-3">
      {roots.map(root => renderNode(root, byParent, ownUuid, theme, { onReply, onEdit, onDelete }, 0))}
    </div>
  )
}
