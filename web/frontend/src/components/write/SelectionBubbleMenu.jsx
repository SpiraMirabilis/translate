import { useState } from 'react'
import { BubbleMenu } from '@tiptap/react/menus'
import { NodeSelection } from '@tiptap/pm/state'
import { CellSelection } from '@tiptap/pm/tables'
import { MarkButtons } from './WriteToolbar'
import LinkPopover from './LinkPopover'

/**
 * Floating format bar over the current text selection. Hidden for node
 * selections (illustrations, rules), multi-cell table drags, and code blocks
 * (marks are illegal there). A plain text selection inside a single table
 * cell still shows it — inline marks are serializable in cells.
 *
 * The link button swaps the bubble's content to the link editor in place, so
 * there's no nested popover to position.
 */
export default function SelectionBubbleMenu({ editor }) {
  const [linkEditing, setLinkEditing] = useState(false)
  if (!editor) return null

  return (
    <BubbleMenu
      editor={editor}
      updateDelay={150}
      options={{ placement: 'top', offset: 8, onHide: () => setLinkEditing(false) }}
      shouldShow={({ editor: ed, state }) => {
        const sel = state.selection
        if (sel.empty) return false
        if (sel instanceof NodeSelection) return false
        if (sel instanceof CellSelection) return false
        if (ed.isActive('codeBlock')) return false
        return ed.isEditable
      }}
      className="z-30 flex items-center gap-0.5 rounded-lg border border-slate-700 bg-slate-800 shadow-xl px-1 py-0.5"
    >
      {linkEditing ? (
        <LinkPopover inline editor={editor} onClose={() => setLinkEditing(false)} />
      ) : (
        <MarkButtons editor={editor} onEditLink={() => setLinkEditing(true)} />
      )}
    </BubbleMenu>
  )
}
