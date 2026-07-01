// Shared Markdown renderer for chapter content (Reader + editor preview).
//
// Chapter content is a list of lines where "" separates paragraphs and
// ⟦IMG:id⟧ lines mark illustrations. To get *block-level* Markdown (lists,
// tables, blockquotes that span multiple lines) while keeping illustrations in
// position, we split the line array into runs separated by marker lines and
// render each run as one Markdown document (run.join('\n')). Blank lines become
// block separators; consecutive non-blank lines stay adjacent, so a run of
// "- …" / "| … |" / "> …" lines groups correctly.
//
// This is a SEPARATE, richer instance from components/MarkdownView.jsx (which
// stays inline-only and tightly locked down for user-submitted comments).
import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'

// Illustration marker: whole-line ⟦IMG:<hex>⟧ (matches illustrations.parse_marker
// and Reader's IMG_MARKER_RE).
const IMG_MARKER_RE = /^\s*⟦IMG:([0-9a-f]{4,})⟧\s*$/

export const markerId = (line) => {
  if (typeof line !== 'string') return null
  const m = line.match(IMG_MARKER_RE)
  return m ? m[1] : null
}

const md = new MarkdownIt({
  html: false,        // never trust raw HTML in content
  linkify: true,
  breaks: true,       // single newline within a block → <br>
  typographer: false,
})
// Disable Markdown image syntax — illustrations ride as ⟦IMG⟧ markers, not ![]().
md.disable(['image'])

const PURIFY_CONFIG = {
  ALLOWED_TAGS: [
    'p', 'br', 'em', 'strong', 'del', 's', 'code', 'pre', 'blockquote', 'hr',
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'ul', 'ol', 'li', 'a',
    'table', 'thead', 'tbody', 'tr', 'th', 'td',
  ],
  ALLOWED_ATTR: ['href', 'target', 'rel', 'align'],
  ALLOWED_URI_REGEXP: /^(?:(?:https?|mailto|tel):|[^a-z]|[a-z+.-]+(?:[^a-z+.\-:]|$))/i,
}

// Harden links: open in a new tab, drop referrer/page-rank.
DOMPurify.addHook('afterSanitizeAttributes', (node) => {
  if (node.tagName === 'A') {
    node.setAttribute('target', '_blank')
    node.setAttribute('rel', 'nofollow noopener noreferrer')
  }
})

/** Render a Markdown document (block-level) to sanitized HTML. */
export function renderBlock(source) {
  if (!source || !source.trim()) return ''
  return DOMPurify.sanitize(md.render(source), PURIFY_CONFIG)
}

/** Render a single line as inline-only Markdown (bold/italic/code/links). */
export function renderInline(source) {
  if (!source || !source.trim()) return ''
  return DOMPurify.sanitize(md.renderInline(source), PURIFY_CONFIG)
}

// Footnote definition line: starts with [n], optional dash, then the note text.
// Inline markers (word[1]) never start a line, so this only catches definitions.
const FN_DEF_RE = /^\s*\[(\d+)\]\s*[-–—]?\s*(.+)$/
// Sentinel for an inline footnote ref, mirroring the ⟦IMG⟧ convention. Plain text,
// so it rides through markdown-it (html:false) and DOMPurify untouched, then gets
// swapped for an anchor post-sanitize. Can't collide with real content.
const FN_SENTINEL_RE = /⟦FN:(\d+)⟧/g

/**
 * Parse footnote definitions from a content line array.
 * Returns { map: { [n]: text }, ids: Set<string> } where `ids` are the numbers
 * that have a matching definition (used to decide which inline markers to linkify).
 */
export function parseFootnotes(lines) {
  const map = {}
  for (const line of lines || []) {
    if (typeof line !== 'string') continue
    const m = line.match(FN_DEF_RE)
    if (m) map[m[1]] = m[2].trim()
  }
  return { map, ids: new Set(Object.keys(map)) }
}

/**
 * Replace inline footnote markers ([n], n ∈ fnIds) with a sentinel token, but ONLY
 * on body lines — definition lines (the bottom [n] … block) are left verbatim so
 * they keep rendering as-is. Returns the line unchanged when there's nothing to do.
 */
export function markFootnoteLine(line, fnIds) {
  if (typeof line !== 'string' || !fnIds || fnIds.size === 0) return line
  if (FN_DEF_RE.test(line)) return line  // definition line — leave the leading [n]
  return line.replace(/\[(\d+)\]/g, (m, n) => (fnIds.has(n) ? `⟦FN:${n}⟧` : m))
}

/** Apply markFootnoteLine across a line array (length/indices preserved). */
export function markFootnoteRefs(lines, fnIds) {
  if (!fnIds || fnIds.size === 0) return lines || []
  return (lines || []).map((line) => markFootnoteLine(line, fnIds))
}

/**
 * Swap footnote sentinels in rendered + sanitized HTML for a clickable control.
 * Runs AFTER DOMPurify (the allowlist strips class/data-*, and html:false escapes
 * raw HTML in source, so this can't be injected pre-render). Only digits are
 * interpolated, so the markup is XSS-safe.
 */
export function linkifyFootnotes(html) {
  if (!html) return html
  return html.replace(FN_SENTINEL_RE, (m, n) =>
    `<a class="footnote-ref" data-fn="${n}" role="button" tabindex="0">[${n}]</a>`)
}

/**
 * Split a content line array into ordered segments:
 *   { type: 'text', md: '<joined markdown source>' }  — a run of lines
 *   { type: 'img', id: '<marker id>' }                — an illustration
 * Text runs are joined with '\n' so the Markdown block model applies.
 */
export function splitSegments(lines) {
  const segments = []
  let run = []
  const flush = () => {
    if (run.length) {
      segments.push({ type: 'text', md: run.join('\n') })
      run = []
    }
  }
  for (const line of lines || []) {
    const id = markerId(line)
    if (id) {
      flush()
      segments.push({ type: 'img', id })
    } else {
      run.push(line)
    }
  }
  flush()
  return segments
}
