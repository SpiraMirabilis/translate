import { useMemo } from 'react'
import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'

// Tight markdown allowlist for user-submitted content. Everything else
// (images, headings, blockquotes, raw HTML, tables) is disabled at the
// parser level. We then run DOMPurify with an explicit tag/attr list as a
// belt-and-suspenders defense.
const md = new MarkdownIt('zero', {
  html: false,
  linkify: true,
  breaks: true,
})
md.enable([
  'paragraph',
  'newline',          // hardbreak via two trailing spaces
  'emphasis',         // *italic* and **bold**
  'backticks',        // `inline code`
  'link',             // [text](url) and autolinked URLs
  'linkify',
  'escape',
])

const PURIFY_CONFIG = {
  ALLOWED_TAGS: ['p', 'em', 'strong', 'code', 'a', 'br'],
  ALLOWED_ATTR: ['href', 'target', 'rel'],
  // Force javascript:/data: schemes to be stripped
  ALLOWED_URI_REGEXP: /^(?:(?:https?|mailto|tel):|[^a-z]|[a-z+.\-]+(?:[^a-z+.\-:]|$))/i,
}

// Tighten links: every <a> opens in a new tab and gets a non-followed,
// no-referrer rel chain so user-supplied links can't pass referrer auth
// or page-rank to the destination.
DOMPurify.addHook('afterSanitizeAttributes', (node) => {
  if (node.tagName === 'A') {
    node.setAttribute('target', '_blank')
    node.setAttribute('rel', 'nofollow noopener noreferrer ugc')
  }
})

export default function MarkdownView({ source, className = '' }) {
  const html = useMemo(() => {
    if (!source) return ''
    const raw = md.render(source)
    return DOMPurify.sanitize(raw, PURIFY_CONFIG)
  }, [source])

  return (
    <div
      className={`markdown-view ${className}`}
      // eslint-disable-next-line react/no-danger
      dangerouslySetInnerHTML={{ __html: html }}
    />
  )
}
