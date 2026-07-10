import { useEffect, useMemo, useRef, memo } from 'react'
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

// ── English overlay backdrop component ───────────────────────────────
const EnglishBackdrop = memo(function EnglishBackdrop({ text, textLines, matcher, scrollTop, paddingClass, searchMatches, activeMatch }) {
  const ref = useRef(null)

  const segments = useMemo(
    () => highlightSegments(text, matcher, true),
    [text, matcher]
  )

  // Sync scroll position with the textarea
  useEffect(() => {
    if (ref.current) ref.current.scrollTop = scrollTop
  }, [scrollTop])

  // Build search highlights per line
  const searchByLine = useMemo(() => {
    if (!searchMatches || Object.keys(searchMatches).length === 0) return null
    return searchMatches
  }, [searchMatches])

  // If we have search matches, render line-by-line with search highlights
  const hasSearchMatches = searchByLine && Object.keys(searchByLine).length > 0

  return (
    <div
      ref={ref}
      className={`absolute inset-0 pointer-events-none overflow-hidden
                 font-mono text-sm leading-relaxed whitespace-pre-wrap
                 ${paddingClass}`}
      style={{ overflowWrap: 'break-word', wordBreak: 'break-word' }}
    >
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
  )
})

export default EnglishBackdrop
