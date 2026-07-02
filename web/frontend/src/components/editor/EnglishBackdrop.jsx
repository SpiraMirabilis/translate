import { useEffect, useMemo, useRef, memo } from 'react'
import { CATEGORY_COLORS } from '../../utils/categories'
import { highlightSegments, applySearchHighlights } from '../../lib/editorHighlights'

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
        // Line-by-line rendering with search highlights
        textLines.map((line, lineIdx) => {
          const lineMatches = searchByLine[lineIdx]
          if (lineMatches && lineMatches.length > 0) {
            const parts = applySearchHighlights(line, lineMatches, activeMatch)
            return (
              <span key={lineIdx}>
                {parts.map((p, j) => {
                  if (p.search) {
                    return (
                      <span
                        key={j}
                        style={{
                          backgroundColor: p.active ? '#f59e0b' : '#fbbf24',
                          borderRadius: '1px',
                        }}
                      >
                        <span style={{ color: 'transparent' }}>{p.text}</span>
                      </span>
                    )
                  }
                  return <span key={j} style={{ color: 'transparent' }}>{p.text}</span>
                })}
                {lineIdx < textLines.length - 1 ? '\n' : ''}
              </span>
            )
          }
          return <span key={lineIdx} style={{ color: 'transparent' }}>{line}{lineIdx < textLines.length - 1 ? '\n' : ''}</span>
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
