import { useMemo, memo } from 'react'
import { CATEGORY_COLORS } from '../../utils/categories'
import { highlightSegments, applySearchHighlights } from '../../lib/editorHighlights'

// ── Highlighted Chinese line component ───────────────────────────────
const HighlightedChineseLine = memo(function HighlightedChineseLine({ line, matcher, annotation, onEntityClick, searchMatches, activeMatch }) {
  const segments = useMemo(
    () => highlightSegments(line, matcher, false),
    [line, matcher]
  )

  const content = segments.map((seg, j) => {
    if (seg.entity) {
      const colors = CATEGORY_COLORS[seg.entity.category] || CATEGORY_COLORS.characters
      return (
        <span
          key={j}
          title={`${seg.entity.translation} (${seg.entity.category}) — click to edit`}
          className="cursor-pointer rounded-sm hover:brightness-150 transition-all"
          style={{
            backgroundColor: colors.bg,
            borderBottom: `1px dashed ${colors.border}`,
          }}
          onClick={(e) => {
            e.stopPropagation()
            onEntityClick?.(seg.entity)
          }}
        >
          {seg.text}
        </span>
      )
    }
    return <span key={j}>{seg.text}</span>
  })

  // Overlay search marks on the whole line (simpler, more reliable approach)
  if (searchMatches && searchMatches.length > 0) {
    const parts = applySearchHighlights(line, searchMatches, activeMatch)
    const searchContent = parts.map((p, j) => {
      if (p.search) {
        return (
          <mark
            key={`s${j}`}
            style={{
              backgroundColor: p.active ? '#f59e0b' : '#fbbf24',
              color: '#1e293b',
              borderRadius: '1px',
              padding: '0 1px',
            }}
          >
            {p.text}
          </mark>
        )
      }
      // For non-search parts, render with entity highlighting
      return <span key={`s${j}`}>{p.text}</span>
    })

    if (annotation) {
      return (
        <ruby className="ruby-annotation">
          {searchContent}
          <rt className="text-emerald-400/90 font-sans text-[0.65em] leading-tight tracking-normal">
            {annotation}
          </rt>
        </ruby>
      )
    }
    return <>{searchContent}</>
  }

  if (annotation) {
    return (
      <ruby className="ruby-annotation">
        {content}
        <rt className="text-emerald-400/90 font-sans text-[0.65em] leading-tight tracking-normal">
          {annotation}
        </rt>
      </ruby>
    )
  }
  return <>{content}</>
})

export default HighlightedChineseLine
