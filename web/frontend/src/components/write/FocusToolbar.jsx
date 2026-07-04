import { useState, useEffect } from 'react'
import { Minimize2, Loader2, Check } from 'lucide-react'
import { MarkButtons, ToolButton, Divider } from './WriteToolbar'
import LinkPopover from './LinkPopover'

/**
 * Minimal auto-hiding toolbar for focus mode: inline marks, a save-state
 * indicator, and the exit button. Reveals when the pointer nears the top of
 * the screen (plus a grace period on entry); hides otherwise so the writing
 * surface stays clean. `pointer-events-none` while hidden keeps the invisible
 * strip from eating clicks.
 */
export default function FocusToolbar({ editor, dirty, saving, savedFlash, onExit }) {
  const [nearTop, setNearTop] = useState(false)
  const [hovered, setHovered] = useState(false)
  const [entering, setEntering] = useState(true)
  const [linkOpen, setLinkOpen] = useState(false)

  useEffect(() => {
    const graceTimer = setTimeout(() => setEntering(false), 1500)
    let raf = null
    const onMove = (e) => {
      if (raf) return
      raf = requestAnimationFrame(() => {
        raf = null
        setNearTop(e.clientY < 80)
      })
    }
    window.addEventListener('mousemove', onMove)
    return () => {
      clearTimeout(graceTimer)
      window.removeEventListener('mousemove', onMove)
      if (raf) cancelAnimationFrame(raf)
    }
  }, [])

  const visible = entering || nearTop || hovered || linkOpen

  return (
    <div
      className={`fixed top-0 inset-x-0 flex items-center gap-0.5 px-3 py-1.5 bg-slate-900/90 backdrop-blur border-b border-slate-800 transition-opacity duration-200 ${
        visible ? 'opacity-100' : 'opacity-0 pointer-events-none'
      }`}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      <span className="relative flex items-center gap-0.5">
        <MarkButtons editor={editor} onEditLink={() => setLinkOpen((v) => !v)} />
        {linkOpen && <LinkPopover editor={editor} onClose={() => setLinkOpen(false)} />}
      </span>
      <Divider />
      <span className="flex items-center px-1.5" title={
        saving ? 'Saving…' : savedFlash ? 'Saved' : dirty ? 'Unsaved changes' : 'Saved'
      }>
        {saving ? (
          <Loader2 size={12} className="animate-spin text-slate-400" />
        ) : savedFlash ? (
          <Check size={12} className="text-emerald-400" />
        ) : dirty ? (
          <span className="w-1.5 h-1.5 rounded-full bg-amber-400/90" />
        ) : (
          <span className="w-1.5 h-1.5 rounded-full bg-emerald-500/60" />
        )}
      </span>
      <div className="flex-1" />
      <ToolButton title="Exit focus mode (Esc)" onClick={onExit}>
        <Minimize2 size={15} />
      </ToolButton>
    </div>
  )
}
