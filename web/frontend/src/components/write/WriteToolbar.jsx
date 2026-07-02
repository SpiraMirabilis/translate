import {
  Bold, Italic, Strikethrough, Code, Quote, Minus, List, ListOrdered,
  Link2, Undo2, Redo2, Save, Loader2, History, Eye, EyeOff, Maximize2,
  Table as TableIcon, Trash2,
} from 'lucide-react'

function ToolButton({ onClick, active, disabled, title, children }) {
  return (
    <button
      type="button"
      className={`p-1.5 rounded text-sm transition-colors ${
        active ? 'bg-indigo-600/40 text-indigo-200' : 'text-slate-400 hover:text-slate-200 hover:bg-slate-700/60'
      } disabled:opacity-40 disabled:pointer-events-none`}
      onClick={onClick}
      disabled={disabled}
      title={title}
      onMouseDown={(e) => e.preventDefault()} /* keep editor focus */
    >
      {children}
    </button>
  )
}

const Divider = () => <div className="w-px h-5 bg-slate-700 mx-1" />

/**
 * Formatting toolbar for the write editor. `tick` is bumped by the parent on
 * every editor update/selection change so isActive states stay fresh.
 */
// eslint-disable-next-line no-unused-vars
export default function WriteToolbar({ editor, tick, saving, dirty, showPreview,
  onSave, onTogglePreview, onToggleRevisions, onToggleFocus }) {
  if (!editor) return null
  const c = () => editor.chain().focus()

  const setLink = () => {
    const prev = editor.getAttributes('link').href || ''
    const url = window.prompt('Link URL', prev)
    if (url === null) return
    if (url === '') c().extendMarkRange('link').unsetLink().run()
    else c().extendMarkRange('link').setLink({ href: url }).run()
  }

  return (
    <div className="flex items-center gap-0.5 flex-wrap">
      <ToolButton title="Bold (Ctrl+B)" active={editor.isActive('bold')}
        onClick={() => c().toggleBold().run()}><Bold size={15} /></ToolButton>
      <ToolButton title="Italic (Ctrl+I)" active={editor.isActive('italic')}
        onClick={() => c().toggleItalic().run()}><Italic size={15} /></ToolButton>
      <ToolButton title="Strikethrough" active={editor.isActive('strike')}
        onClick={() => c().toggleStrike().run()}><Strikethrough size={15} /></ToolButton>
      <ToolButton title="Inline code" active={editor.isActive('code')}
        onClick={() => c().toggleCode().run()}><Code size={15} /></ToolButton>
      <Divider />
      {[1, 2, 3].map((level) => (
        <ToolButton key={level} title={`Heading ${level}`}
          active={editor.isActive('heading', { level })}
          onClick={() => c().toggleHeading({ level }).run()}>
          <span className="font-bold text-xs w-[18px] inline-block text-center">H{level}</span>
        </ToolButton>
      ))}
      <Divider />
      <ToolButton title="Blockquote" active={editor.isActive('blockquote')}
        onClick={() => c().toggleBlockquote().run()}><Quote size={15} /></ToolButton>
      <ToolButton title="Bullet list" active={editor.isActive('bulletList')}
        onClick={() => c().toggleBulletList().run()}><List size={15} /></ToolButton>
      <ToolButton title="Numbered list" active={editor.isActive('orderedList')}
        onClick={() => c().toggleOrderedList().run()}><ListOrdered size={15} /></ToolButton>
      <ToolButton title="Scene break (horizontal rule)"
        onClick={() => c().setHorizontalRule().run()}><Minus size={15} /></ToolButton>
      <ToolButton title="Link" active={editor.isActive('link')} onClick={setLink}>
        <Link2 size={15} /></ToolButton>
      <ToolButton title="Insert table" active={editor.isActive('table')}
        onClick={() => c().insertTable({ rows: 2, cols: 2, withHeaderRow: true }).run()}>
        <TableIcon size={15} /></ToolButton>
      {editor.isActive('table') && (
        <span className="flex items-center gap-0.5 px-1 rounded bg-slate-800/80">
          <ToolButton title="Add row below" onClick={() => c().addRowAfter().run()}>
            <span className="text-[10px] font-semibold px-0.5">+Row</span></ToolButton>
          <ToolButton title="Delete row" onClick={() => c().deleteRow().run()}>
            <span className="text-[10px] font-semibold px-0.5">−Row</span></ToolButton>
          <ToolButton title="Add column right" onClick={() => c().addColumnAfter().run()}>
            <span className="text-[10px] font-semibold px-0.5">+Col</span></ToolButton>
          <ToolButton title="Delete column" onClick={() => c().deleteColumn().run()}>
            <span className="text-[10px] font-semibold px-0.5">−Col</span></ToolButton>
          <ToolButton title="Delete table" onClick={() => c().deleteTable().run()}>
            <Trash2 size={13} /></ToolButton>
        </span>
      )}
      <Divider />
      <ToolButton title="Undo (Ctrl+Z)" disabled={!editor.can().undo()}
        onClick={() => c().undo().run()}><Undo2 size={15} /></ToolButton>
      <ToolButton title="Redo (Ctrl+Shift+Z)" disabled={!editor.can().redo()}
        onClick={() => c().redo().run()}><Redo2 size={15} /></ToolButton>
      <Divider />
      <ToolButton title={showPreview ? 'Back to editing' : 'Reader preview'} active={showPreview}
        onClick={onTogglePreview}>{showPreview ? <EyeOff size={15} /> : <Eye size={15} />}</ToolButton>
      <ToolButton title="Revision history" onClick={onToggleRevisions}>
        <History size={15} /></ToolButton>
      <ToolButton title="Focus mode (Ctrl+Shift+F, Esc exits)" onClick={onToggleFocus}>
        <Maximize2 size={15} /></ToolButton>
      <div className="flex-1" />
      <button
        type="button"
        className="btn-primary flex items-center gap-1.5 text-sm px-3 py-1.5"
        onClick={onSave}
        disabled={saving || !dirty}
        title="Save (Ctrl+S) — records a revision"
      >
        {saving ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />}
        Save
      </button>
    </div>
  )
}
