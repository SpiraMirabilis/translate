import { useState, useEffect, useRef } from 'react'
import { Check, Trash2, ExternalLink } from 'lucide-react'

/**
 * Inline link editor for the write editor — replaces window.prompt. Used two
 * ways: `inline` as the content of the selection bubble menu, otherwise as a
 * small panel anchored under the toolbar's link button (nearest `relative`
 * ancestor) with a click-away backdrop.
 *
 * `data-esc-guard` + stopPropagation keep Escape local: it closes the popover
 * without also exiting focus mode (WriteEditor's window handler skips Esc
 * while any guard element is mounted).
 */
export default function LinkPopover({ editor, onClose, inline = false }) {
  const current = editor.getAttributes('link').href || ''
  const [href, setHref] = useState(current)
  const inputRef = useRef(null)

  useEffect(() => {
    inputRef.current?.focus()
    inputRef.current?.select()
  }, [])

  const apply = () => {
    const url = href.trim()
    const chain = editor.chain().focus().extendMarkRange('link')
    if (!url) chain.unsetLink().run()
    else chain.setLink({ href: /^[a-z][a-z0-9+.-]*:/i.test(url) ? url : `https://${url}` }).run()
    onClose()
  }

  const remove = () => {
    editor.chain().focus().extendMarkRange('link').unsetLink().run()
    onClose()
  }

  const body = (
    <div
      data-esc-guard
      className={`flex items-center gap-1.5 p-1.5 ${
        inline ? '' : 'rounded-lg border border-slate-700 bg-slate-800 shadow-xl'
      }`}
      onKeyDown={(e) => {
        if (e.key === 'Enter') { e.preventDefault(); apply() }
        if (e.key === 'Escape') { e.stopPropagation(); onClose() }
      }}
    >
      <input
        ref={inputRef}
        className="input text-xs px-2 py-1 w-52"
        placeholder="https://…"
        value={href}
        onChange={(e) => setHref(e.target.value)}
      />
      <button type="button" className="btn-primary p-1.5" title="Apply (Enter)" onClick={apply}>
        <Check size={13} />
      </button>
      {editor.isActive('link') && (
        <button type="button" className="btn-ghost p-1.5 text-slate-400 hover:text-rose-300"
          title="Remove link" onClick={remove}>
          <Trash2 size={13} />
        </button>
      )}
      {current && (
        <button type="button" className="btn-ghost p-1.5 text-slate-400 hover:text-slate-200"
          title="Open link in new tab"
          onClick={() => window.open(current, '_blank', 'noopener')}>
          <ExternalLink size={13} />
        </button>
      )}
    </div>
  )

  if (inline) return body
  return (
    <>
      <div className="fixed inset-0 z-20" onClick={onClose} />
      <div className="absolute left-0 top-full mt-1 z-30">{body}</div>
    </>
  )
}
