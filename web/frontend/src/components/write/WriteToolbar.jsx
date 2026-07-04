import { useState } from 'react'
import {
  Bold, Italic, Strikethrough, Code, Quote, Minus, List, ListOrdered,
  Link2, Undo2, Redo2, Save, Loader2, History, Eye, EyeOff, Maximize2,
  Table as TableIcon, Trash2, ClipboardCopy, SpellCheck, Sparkles,
  ChevronLeft, ChevronRight, X,
} from 'lucide-react'
import LinkPopover from './LinkPopover'

export function ToolButton({ onClick, active, disabled, title, children }) {
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

export const Divider = () => <div className="w-px h-5 bg-slate-700 mx-1" />

/**
 * The inline-mark button group (bold/italic/strike/code/link), shared by the
 * main toolbar, the selection bubble menu, and the focus-mode toolbar. The
 * container decides how `onEditLink` presents the link editor.
 */
export function MarkButtons({ editor, onEditLink }) {
  const c = () => editor.chain().focus()
  return (
    <>
      <ToolButton title="Bold (Ctrl+B)" active={editor.isActive('bold')}
        onClick={() => c().toggleBold().run()}><Bold size={15} /></ToolButton>
      <ToolButton title="Italic (Ctrl+I)" active={editor.isActive('italic')}
        onClick={() => c().toggleItalic().run()}><Italic size={15} /></ToolButton>
      <ToolButton title="Strikethrough" active={editor.isActive('strike')}
        onClick={() => c().toggleStrike().run()}><Strikethrough size={15} /></ToolButton>
      <ToolButton title="Inline code" active={editor.isActive('code')}
        onClick={() => c().toggleCode().run()}><Code size={15} /></ToolButton>
      <ToolButton title="Link" active={editor.isActive('link')} onClick={onEditLink}>
        <Link2 size={15} /></ToolButton>
    </>
  )
}

/**
 * Formatting toolbar for the write editor. `tick` is bumped by the parent on
 * every editor update/selection change so isActive states stay fresh.
 */
// eslint-disable-next-line no-unused-vars
export default function WriteToolbar({ editor, tick, saving, dirty, showPreview,
  onSave, onTogglePreview, onToggleRevisions, onToggleFocus,
  onCopyBBCode, bbcodeCopied, grammar }) {
  const [linkOpen, setLinkOpen] = useState(false)
  if (!editor) return null
  const c = () => editor.chain().focus()

  return (
    <div className="flex items-center gap-0.5 flex-wrap">
      <span className="relative flex items-center gap-0.5">
        <MarkButtons editor={editor} onEditLink={() => setLinkOpen((v) => !v)} />
        {linkOpen && <LinkPopover editor={editor} onClose={() => setLinkOpen(false)} />}
      </span>
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
      <ToolButton title="Insert table" active={editor.isActive('table')}
        onClick={() => c().insertTable({ rows: 2, cols: 2, withHeaderRow: true }).run()}>
        <TableIcon size={15} /></ToolButton>
      {editor.isActive('table') && (
        <span className="flex items-center gap-0.5 px-1 rounded bg-slate-800/80">
          <ToolButton title="Toggle header row (markdown tables need one — saves are blocked without it)"
            onClick={() => c().toggleHeaderRow().run()}>
            <span className="text-[10px] font-semibold px-0.5">Hdr</span></ToolButton>
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
      <ToolButton title={showPreview ? 'Back to editing (Ctrl+Alt+P)' : 'Reader preview (Ctrl+Alt+P)'} active={showPreview}
        onClick={onTogglePreview}>{showPreview ? <EyeOff size={15} /> : <Eye size={15} />}</ToolButton>
      <ToolButton title="Revision history (Ctrl+Alt+H)" onClick={onToggleRevisions}>
        <History size={15} /></ToolButton>
      <ToolButton title="Copy chapter as BBCode (SpaceBattles / SV / QQ)" onClick={onCopyBBCode}>
        <ClipboardCopy size={15} className={bbcodeCopied ? 'text-emerald-400' : undefined} /></ToolButton>
      <ToolButton title="Focus mode (Ctrl+Shift+F, Esc exits)" onClick={onToggleFocus}>
        <Maximize2 size={15} /></ToolButton>
      {grammar?.enabled && (
        <>
          <Divider />
          <ToolButton
            title={grammar.ltDown
              ? 'Grammar service unavailable — click to retry'
              : 'Check grammar & spelling now'}
            onClick={grammar.checkNow}
            disabled={grammar.checking}
          >
            {grammar.checking
              ? <Loader2 size={15} className="animate-spin" />
              : <SpellCheck size={15} className={grammar.ltDown ? 'text-slate-600' : undefined} />}
          </ToolButton>
          <ToolButton
            title={grammar.polishing
              ? 'Polishing… (runs in the background — you can navigate away and come back)'
              : 'LLM polish pass — per-suggestion review'}
            onClick={grammar.runPolish}
            disabled={grammar.polishing}
          >
            {grammar.polishing
              ? <Loader2 size={15} className="animate-spin text-purple-300" />
              : <Sparkles size={15} />}
          </ToolButton>
          {grammar.polishStats && (
            <span className="flex items-center gap-0.5 px-1.5 py-0.5 rounded bg-purple-500/15 border border-purple-600/40 text-xs text-purple-200">
              ✦ {grammar.polishStats.remaining}/{grammar.polishStats.total}
              <button type="button" className="p-0.5 hover:text-white" title="Previous suggestion"
                onMouseDown={(e) => e.preventDefault()} onClick={() => grammar.navigatePolish(-1)}>
                <ChevronLeft size={12} /></button>
              <button type="button" className="p-0.5 hover:text-white" title="Next suggestion"
                onMouseDown={(e) => e.preventDefault()} onClick={() => grammar.navigatePolish(1)}>
                <ChevronRight size={12} /></button>
              <button type="button" className="p-0.5 hover:text-white" title="Clear polish suggestions"
                onClick={grammar.clearPolish}><X size={12} /></button>
            </span>
          )}
          {grammar.polishError && (
            <span className="text-xs text-slate-500 px-1">{grammar.polishError}</span>
          )}
        </>
      )}
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
