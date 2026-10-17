import { useLayoutEffect, useMemo, useRef, memo } from 'react'
import { CATEGORY_COLORS } from '../../utils/categories'
import { highlightSegments } from '../../lib/editorHighlights'

// Merge entity segments and search-match ranges for one line into flat
// intervals so search marks can overlay entity marks. Spans are split at
// every boundary, so a part can be entity-only, search-only, or both.
function mergeLineHighlights(line, matcher, lineMatches, activeMatch) {
  const entityRanges = []
  let pos = 0
  for (const seg of highlightSegments(line, matcher, true)) {
    if (seg.entity) entityRanges.push({ start: pos, end: pos + seg.text.length, entity: seg.entity })
    pos += seg.text.length
  }
  const searchRanges = (lineMatches || [])
    .map(m => ({
      start: m.col,
      end: Math.min(m.col + m.length, line.length),
      active: !!(activeMatch && m.col === activeMatch.col && m.length === activeMatch.length && m.field === activeMatch.field),
    }))
    .filter(r => r.end > r.start)

  const cuts = new Set([0, line.length])
  for (const r of entityRanges) { cuts.add(r.start); cuts.add(r.end) }
  for (const r of searchRanges) { cuts.add(r.start); cuts.add(r.end) }
  const points = [...cuts].sort((a, b) => a - b)

  const parts = []
  for (let i = 0; i < points.length - 1; i++) {
    const a = points[i], b = points[i + 1]
    if (a >= b) continue
    const entity = entityRanges.find(r => r.start <= a && b <= r.end)?.entity || null
    const search = searchRanges.find(r => r.start <= a && b <= r.end) || null
    parts.push({ text: line.slice(a, b), entity, search: !!search, active: !!search?.active })
  }
  return parts.length ? parts : [{ text: line, entity: null, search: false, active: false }]
}

// ── CSS Custom Highlight API path ────────────────────────────────────
// Wrapping spans around highlighted words changes where lines wrap: Chrome
// rounds each inline piece's width separately, so a line that exactly fills
// the box in the textarea (one text run) can overflow by a fraction of a
// pixel in the backdrop and wrap — measured at 1440px wide, where the text
// box is exactly 75 JetBrains Mono columns. With ::highlight the backdrop is
// a single text node, laid out exactly like the textarea's value. Browsers
// without it (Firefox < 140, Safari < 17.2) keep the span rendering.
const SUPPORTS_HIGHLIGHTS = typeof CSS !== 'undefined' && !!CSS.highlights && typeof Highlight !== 'undefined'
const HL_PREFIX = 't9-editor-'
const CATEGORY_KEYS = Object.keys(CATEGORY_COLORS)
const HL_STYLES = [
  ...CATEGORY_KEYS.map(cat => {
    const c = CATEGORY_COLORS[cat]
    return `::highlight(${HL_PREFIX}ent-${cat}) { background-color: ${c.bg}; text-decoration: underline dashed ${c.border} 1px; text-underline-offset: 3px; }`
  }),
  `::highlight(${HL_PREFIX}search) { background-color: #fbbf24; }`,
  `::highlight(${HL_PREFIX}search-active) { background-color: #f59e0b; }`,
].join('\n')

function useEditorHighlights(nodeRef, text, textLines, matcher, searchMatches, activeMatch) {
  useLayoutEffect(() => {
    if (!SUPPORTS_HIGHLIGHTS) return
    const node = nodeRef.current?.firstChild
    const names = []
    const set = (name, ranges, priority) => {
      if (!ranges.length) return
      const h = new Highlight(...ranges)
      h.priority = priority
      CSS.highlights.set(HL_PREFIX + name, h)
      names.push(HL_PREFIX + name)
    }
    if (node && node.nodeType === Node.TEXT_NODE && node.data === text) {
      const range = (start, end) => { const r = new Range(); r.setStart(node, start); r.setEnd(node, end); return r }
      const byCat = {}
      let pos = 0
      for (const seg of highlightSegments(text, matcher, true)) {
        if (seg.entity) {
          const cat = CATEGORY_COLORS[seg.entity.category] ? seg.entity.category : 'characters'
          ;(byCat[cat] ||= []).push(range(pos, pos + seg.text.length))
        }
        pos += seg.text.length
      }
      for (const [cat, ranges] of Object.entries(byCat)) set(`ent-${cat}`, ranges, 0)

      const search = [], active = []
      if (searchMatches) {
        let lineStart = 0
        textLines.forEach((line, i) => {
          for (const m of searchMatches[i] || []) {
            const end = Math.min(m.col + m.length, line.length)
            if (end <= m.col) continue
            const isActive = activeMatch && m.col === activeMatch.col && m.length === activeMatch.length && m.field === activeMatch.field
            ;(isActive ? active : search).push(range(lineStart + m.col, lineStart + end))
          }
          lineStart += line.length + 1
        })
      }
      set('search', search, 1)
      set('search-active', active, 2)
    }
    return () => { for (const n of names) CSS.highlights.delete(n) }
  }, [nodeRef, text, textLines, matcher, searchMatches, activeMatch])
}

// ── English overlay backdrop component ───────────────────────────────
// Sits under the textarea and must wrap and scroll exactly like it:
//  - `scrollbarGutter: stable` here and on the textarea reserves the same
//    gutter in both, so the text wraps at the same width. Without it the
//    backdrop (overflow hidden, no scrollbar) was wider by the scrollbar —
//    sometimes harmless with the 6px ::-webkit-scrollbar, but Firefox draws
//    its own ~12-16px bar, and either is enough to wrap lines differently.
//  - Scroll is a transform on the inner div, not scrollTop: scrollTop clamps
//    to the backdrop's own height, so any height difference (a stale debounced
//    text, a trailing newline) froze the highlights near the bottom of the
//    chapter while the text kept moving.
//  - No spans in the text where the browser supports ::highlight (above).
// The parent writes the transform directly on scroll (applyOverlayScroll);
// `scrollTop` only seeds it on mount / re-render.
const EnglishBackdrop = memo(function EnglishBackdrop({ text, textLines, matcher, scrollTop, paddingClass, searchMatches, activeMatch }) {
  const contentRef = useRef(null)
  useEditorHighlights(contentRef, text, textLines, matcher, searchMatches, activeMatch)

  const segments = useMemo(
    () => SUPPORTS_HIGHLIGHTS ? null : highlightSegments(text, matcher, true),
    [text, matcher]
  )

  // Build search highlights per line
  const searchByLine = useMemo(() => {
    if (!searchMatches || Object.keys(searchMatches).length === 0) return null
    return searchMatches
  }, [searchMatches])

  // If we have search matches, render line-by-line with search highlights
  const hasSearchMatches = searchByLine && Object.keys(searchByLine).length > 0

  const rootClass = `absolute inset-0 pointer-events-none overflow-hidden
                 font-mono text-sm leading-relaxed whitespace-pre-wrap
                 ${paddingClass}`
  const rootStyle = { overflowWrap: 'break-word', wordBreak: 'normal', scrollbarGutter: 'stable' }

  if (SUPPORTS_HIGHLIGHTS) {
    return (
      <div className={rootClass} style={rootStyle}>
        <div ref={contentRef} data-backdrop-content style={{ transform: `translateY(-${scrollTop}px)`, color: 'transparent' }}>{text}</div>
        <style>{HL_STYLES}</style>
      </div>
    )
  }

  return (
    <div className={rootClass} style={rootStyle}>
     <div data-backdrop-content style={{ transform: `translateY(-${scrollTop}px)` }}>
      {hasSearchMatches ? (
        // Line-by-line rendering: entity highlights AND search highlights.
        // Search marks overlay entity marks (the search background wins on
        // overlap; the entity's dashed underline is kept).
        textLines.map((line, lineIdx) => {
          const parts = mergeLineHighlights(line, matcher, searchByLine[lineIdx], activeMatch)
          return (
            <span key={lineIdx}>
              {parts.map((p, j) => {
                if (!p.entity && !p.search) {
                  return <span key={j} style={{ color: 'transparent' }}>{p.text}</span>
                }
                const colors = p.entity
                  ? (CATEGORY_COLORS[p.entity.category] || CATEGORY_COLORS.characters)
                  : null
                const style = { borderRadius: p.search ? '1px' : '2px' }
                if (colors) {
                  style.backgroundColor = colors.bg
                  style.borderBottom = `1px dashed ${colors.border}`
                }
                if (p.search) {
                  style.backgroundColor = p.active ? '#f59e0b' : '#fbbf24'
                }
                return (
                  <span key={j} style={style}>
                    <span style={{ color: 'transparent' }}>{p.text}</span>
                  </span>
                )
              })}
              {lineIdx < textLines.length - 1 ? '\n' : ''}
            </span>
          )
        })
      ) : (
        // Original entity-only rendering
        segments.map((seg, i) => {
          if (seg.entity) {
            const colors = CATEGORY_COLORS[seg.entity.category] || CATEGORY_COLORS.characters
            return (
              <span
                key={i}
                style={{
                  backgroundColor: colors.bg,
                  borderBottom: `1px dashed ${colors.border}`,
                  borderRadius: '2px',
                }}
              >
                <span style={{ color: 'transparent' }}>{seg.text}</span>
              </span>
            )
          }
          return <span key={i} style={{ color: 'transparent' }}>{seg.text}</span>
        })
      )}
     </div>
    </div>
  )
})

export default EnglishBackdrop
