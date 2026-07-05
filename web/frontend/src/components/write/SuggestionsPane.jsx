import { useEffect, useRef, useState } from 'react'
import { X, Settings2, EyeOff, RotateCcw, BookPlus, Globe } from 'lucide-react'
import { KINDS, KIND_LABEL, KIND_DOT } from '../../lib/grammarKinds'

/**
 * Docked right-hand pane for working sequentially through grammar + polish
 * suggestions. The list derives from the plugin's DecorationSet (via
 * grammar.issues), so positions track edits automatically. Kind chips are a
 * view filter (squiggles hide, counts hold); "Ignore rule" silences a
 * LanguageTool rule permanently (localStorage, global across books).
 *
 * Focus stays on the pane after actions so ArrowUp/Down + Enter can chew
 * through the list without touching the mouse.
 */
export default function SuggestionsPane({ grammar, onClose }) {
  const containerRef = useRef(null)
  const [showIgnored, setShowIgnored] = useState(false)
  const [dictAdded, setDictAdded] = useState(() => new Set())

  const visible = grammar.issues.filter((i) => grammar.kindVisibility[i.kind] !== false)
  const counts = {}
  for (const k of KINDS) counts[k] = 0
  for (const i of grammar.issues) counts[i.kind] = (counts[i.kind] || 0) + 1

  const selected = visible.find((i) => i.id === grammar.activeId) || null

  // After an action removes row(s), select the next surviving row (wrapping
  // back to the previous one at the end of the list). Computed from the
  // filtered list so the advance order matches what the user sees.
  const advanceFrom = (fromId, removedPred = () => false) => {
    const idx = visible.findIndex((i) => i.id === fromId)
    if (idx === -1) return
    const next = visible.slice(idx + 1).find((i) => !removedPred(i))
      || [...visible.slice(0, idx)].reverse().find((i) => !removedPred(i))
    if (next) grammar.selectIssue(next.id)
  }
  const refocus = () => containerRef.current?.focus({ preventScroll: true })

  const handleApply = (issue, replacement) => {
    grammar.applySuggestion(issue.id, replacement)
    advanceFrom(issue.id, (i) => i.id === issue.id)
    refocus()
  }
  const handleDismiss = (issue) => {
    grammar.dismiss(issue.id)
    advanceFrom(issue.id, (i) => i.id === issue.id)
    refocus()
  }
  const handleIgnoreRule = (issue) => {
    grammar.ignoreRule(issue)
    advanceFrom(issue.id, (i) => i.ruleId === issue.ruleId)
    refocus()
  }
  const handleAddToDictionary = (issue, opts) => {
    setDictAdded((prev) => new Set(prev).add(issue.id))
    grammar.addToDictionary(issue.originalText, opts)
    advanceFrom(issue.id, (i) =>
      i.kind === 'typo' && i.originalText.toLowerCase() === issue.originalText.toLowerCase())
    refocus()
  }
  const selectRelative = (dir) => {
    if (!visible.length) return
    const idx = selected ? visible.findIndex((i) => i.id === selected.id) : -1
    const next = idx === -1
      ? (dir > 0 ? visible[0] : visible[visible.length - 1])
      : visible[(idx + dir + visible.length) % visible.length]
    grammar.selectIssue(next.id)
  }

  const handleKeyDown = (e) => {
    if (e.target.closest('input, textarea')) return
    if (e.key === 'ArrowDown' || e.key === 'j') {
      e.preventDefault()
      selectRelative(1)
    } else if (e.key === 'ArrowUp' || e.key === 'k') {
      e.preventDefault()
      selectRelative(-1)
    } else if (e.key === 'Enter' && selected) {
      e.preventDefault()
      if (selected.replacements.length) handleApply(selected, selected.replacements[0])
    } else if ((e.key === 'Delete' || e.key === 'Backspace') && selected) {
      e.preventDefault()
      handleDismiss(selected)
    } else if (e.key === 'Escape') {
      e.stopPropagation()
      onClose()
    }
  }

  // Squiggle clicks (and next/prev elsewhere) select rows out from under us —
  // keep the selected row in view.
  useEffect(() => {
    if (!grammar.activeId || !containerRef.current) return
    containerRef.current
      .querySelector(`[data-issue-id="${CSS.escape(grammar.activeId)}"]`)
      ?.scrollIntoView({ block: 'nearest' })
  }, [grammar.activeId])

  return (
    <div
      ref={containerRef}
      tabIndex={0}
      onKeyDown={handleKeyDown}
      className="hidden md:flex w-80 lg:w-96 shrink-0 self-start sticky top-12 md:top-2
        max-h-[calc(100vh-4rem)] md:max-h-[calc(100vh-1rem)] overflow-y-auto
        rounded-lg border border-slate-700/60 bg-slate-800/80 flex-col
        outline-none focus-visible:ring-1 focus-visible:ring-indigo-500/60"
    >
      {/* Header */}
      <div className="sticky top-0 z-10 bg-slate-800 rounded-t-lg border-b border-slate-700/60 px-3 py-2">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-semibold text-slate-200 flex-1">
            Suggestions
            <span className="ml-1.5 text-xs font-normal text-slate-400">{visible.length}</span>
          </h3>
          <button
            type="button"
            className={`btn-ghost p-1 ${showIgnored ? 'text-indigo-300' : 'text-slate-400 hover:text-slate-200'}`}
            title="Ignored rules"
            onClick={() => setShowIgnored((v) => !v)}
          >
            <Settings2 size={14} />
          </button>
          <button type="button" className="btn-ghost p-1" title="Close (Esc)" onClick={onClose}>
            <X size={14} />
          </button>
        </div>
        {/* Kind filter chips — counts from the full list so they stay honest */}
        <div className="flex flex-wrap gap-1 mt-1.5">
          {KINDS.map((k) => {
            const on = grammar.kindVisibility[k] !== false
            return (
              <button
                key={k}
                type="button"
                title={on ? `Hide ${KIND_LABEL[k].toLowerCase()} suggestions` : `Show ${KIND_LABEL[k].toLowerCase()} suggestions`}
                className={`flex items-center gap-1 px-1.5 py-0.5 rounded text-xs border transition-colors ${
                  on
                    ? 'border-slate-600 bg-slate-700/60 text-slate-200'
                    : 'border-slate-700/60 text-slate-500 line-through'
                }`}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => grammar.setKindVisibility((prev) => ({ ...prev, [k]: !on }))}
              >
                <span className={`w-1.5 h-1.5 rounded-full ${on ? KIND_DOT[k] : 'bg-slate-600'}`} />
                {KIND_LABEL[k]}
                <span className={on ? 'text-slate-400' : 'text-slate-600'}>{counts[k]}</span>
              </button>
            )
          })}
        </div>
      </div>

      {/* Ignored-rules manager */}
      {showIgnored && (
        <div className="border-b border-slate-700/60 px-3 py-2 space-y-1 bg-slate-900/40">
          <p className="text-[11px] uppercase tracking-wide text-slate-500 font-medium">
            Ignored rules (all books)
          </p>
          {grammar.ignoredRules.length === 0 && (
            <p className="text-xs text-slate-500">
              No ignored rules. Use “Ignore rule” on a suggestion to silence its rule everywhere.
            </p>
          )}
          {grammar.ignoredRules.map((r) => (
            <div key={r.ruleId} className="flex items-center gap-2 text-xs" title={r.ruleId}>
              <span className="flex-1 min-w-0 truncate text-slate-300">
                {r.categoryName && <span className="text-slate-500">{r.categoryName} · </span>}
                {r.sampleMessage || r.ruleId}
              </span>
              <button
                type="button"
                className="btn-ghost p-1 text-slate-400 hover:text-slate-200"
                title="Stop ignoring this rule"
                onClick={() => grammar.unignoreRule(r.ruleId)}
              >
                <RotateCcw size={12} />
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Issue list */}
      <div className="flex-1 p-1.5 space-y-1">
        {visible.length === 0 && (
          <p className="text-xs text-slate-500 px-2 py-6 text-center">
            {grammar.issues.length
              ? 'All suggestions are hidden by the filters above.'
              : 'No suggestions. Run a grammar check or polish pass from the toolbar.'}
          </p>
        )}
        {visible.map((issue) => {
          const isSel = issue.id === grammar.activeId
          return (
            <div
              key={issue.id}
              data-issue-id={issue.id}
              className={`rounded-md border px-2 py-1.5 cursor-pointer transition-colors ${
                isSel
                  ? 'border-indigo-500/60 bg-slate-700/50'
                  : 'border-transparent hover:bg-slate-700/30'
              }`}
              onClick={() => !isSel && grammar.selectIssue(issue.id)}
            >
              <div className="flex items-start gap-1.5">
                <span className={`mt-1 w-1.5 h-1.5 rounded-full shrink-0 ${KIND_DOT[issue.kind] || 'bg-slate-400'}`} />
                <div className="flex-1 min-w-0">
                  <p className="text-xs font-medium text-slate-300 truncate">
                    {issue.shortMessage || KIND_LABEL[issue.kind] || 'Issue'}
                  </p>
                  <p
                    className="text-xs text-slate-400 truncate cursor-pointer hover:text-slate-300"
                    title="Jump to this spot in the editor"
                    onClick={(e) => {
                      e.stopPropagation()
                      grammar.jumpToIssue(issue.id)
                    }}
                  >
                    {issue.excerpt.before && <span>…{issue.excerpt.before}</span>}
                    <span className="text-slate-200 bg-slate-600/50 rounded px-0.5">{issue.excerpt.text}</span>
                    {issue.excerpt.after && <span>{issue.excerpt.after}…</span>}
                  </p>
                </div>
              </div>

              {isSel && (
                <div className="mt-1.5 pl-3 space-y-1.5" onClick={(e) => e.stopPropagation()}>
                  {issue.message && issue.message !== issue.shortMessage && (
                    <p className="text-xs text-slate-400">{issue.message}</p>
                  )}
                  {issue.replacements.length > 0 && (
                    <div className="flex flex-wrap gap-1.5">
                      {issue.replacements.map((r, i) => (
                        <button
                          key={i}
                          type="button"
                          className="btn-secondary text-xs px-2 py-1 font-medium text-emerald-300"
                          title={i === 0 ? 'Apply (Enter)' : 'Apply'}
                          onMouseDown={(e) => e.preventDefault()}
                          onClick={() => handleApply(issue, r)}
                        >
                          {r === '' ? '(delete)' : r}
                        </button>
                      ))}
                    </div>
                  )}
                  <div className="flex items-center gap-1.5 flex-wrap">
                    <button
                      type="button"
                      className="btn-ghost text-xs px-1.5 py-1 text-slate-400 hover:text-slate-200"
                      title="Dismiss (Del)"
                      onMouseDown={(e) => e.preventDefault()}
                      onClick={() => handleDismiss(issue)}
                    >
                      Dismiss
                    </button>
                    {issue.source === 'lt' && issue.ruleId && (
                      <button
                        type="button"
                        className="btn-ghost text-xs px-1.5 py-1 flex items-center gap-1 text-slate-400 hover:text-slate-200"
                        title={`Never show this rule again (${issue.categoryName ? `${issue.categoryName} · ` : ''}${issue.ruleId})`}
                        onMouseDown={(e) => e.preventDefault()}
                        onClick={() => handleIgnoreRule(issue)}
                      >
                        <EyeOff size={12} /> Ignore rule
                      </button>
                    )}
                    {issue.kind === 'typo' && issue.originalText && !/\s/.test(issue.originalText) && (
                      <>
                        <div className="flex-1" />
                        <button
                          type="button"
                          className="btn-ghost text-xs px-1.5 py-1 flex items-center gap-1 text-slate-400 hover:text-slate-200"
                          title="Add to this book's dictionary"
                          disabled={dictAdded.has(issue.id)}
                          onMouseDown={(e) => e.preventDefault()}
                          onClick={() => handleAddToDictionary(issue)}
                        >
                          <BookPlus size={12} />
                        </button>
                        <button
                          type="button"
                          className="btn-ghost p-1 text-slate-500 hover:text-slate-200"
                          title="Add to the global dictionary (all books)"
                          disabled={dictAdded.has(issue.id)}
                          onMouseDown={(e) => e.preventDefault()}
                          onClick={() => handleAddToDictionary(issue, { global: true })}
                        >
                          <Globe size={12} />
                        </button>
                      </>
                    )}
                  </div>
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
