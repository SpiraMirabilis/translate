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

// Rich-table sentinel markers (whole-line), bbcode-style with explicit
// terminators — the XenForo table model. Cells hold ordinary content lines
// (blank line = paragraph break, adjacent lines = hard breaks, lists/quotes
// work), so a cell can span multiple lines, which pipe tables can't express.
// Mirrored in output_formatter.py (_TABLE_MARKER_RE etc.).
export const TABLE_MARKER_RE = /^\s*⟦\/?(TABLE|TR|TH|TD)(?::(left|center|right))?⟧\s*$/
const TBL_OPEN_RE = /^\s*⟦TABLE⟧\s*$/
const TR_OPEN_RE = /^\s*⟦TR⟧\s*$/
const CELL_OPEN_RE = /^\s*⟦(TH|TD)(?::(left|center|right))?⟧\s*$/
const CLOSE_RE = /^\s*⟦\/(TABLE|TR|TH|TD)⟧\s*$/

// Inline formatting sentinels — underline and foreground color, which have no
// markdown form: ⟦U⟧…⟦/U⟧ and ⟦COLOR:#rrggbb⟧…⟦/COLOR⟧. Plain text through
// markdown-it (html:false) and the sanitizers, swapped for real tags AFTER
// sanitization (the ⟦FN⟧ pattern). Only validated hex is interpolated, so the
// markup is XSS-safe. Mirrored in output_formatter.py and mapped 1:1 to
// XenForo [U]/[COLOR=…] by bbcode.js.
const INLINE_SENTINEL_SRC = '⟦(\\/?)(U|COLOR)(?::(#[0-9a-f]{6}))?⟧'
export const INLINE_SENTINEL_RE = new RegExp(INLINE_SENTINEL_SRC)
// Block-level boundaries a pair may never cross (open/close would emit
// mismatched tags across elements). <code>/<pre> are handled separately.
const BLOCK_TAG_RE = /<\/?(?:p|li|ul|ol|blockquote|h[1-6]|t[dhr]|table|thead|tbody|pre|div)\b/i
const CODE_REGION_RE = /<pre\b[\s\S]*?<\/pre>|<code\b[\s\S]*?<\/code>/g

/**
 * Replace balanced ⟦U⟧/⟦COLOR:#hex⟧ pairs in a rendered string. `tags` maps
 * markers to output: { uOpen, uClose, colorOpen(hex), colorClose }. `guards`
 * (optional) = { code, block }: markers inside a `code` region stay literal
 * (a pair may still span a whole code region) and pairs whose interior
 * matches `block` are left literal (mismatched tags across block elements).
 * Unmatched or misnested markers always stay literal.
 */
export function replaceInlineSentinels(str, tags, guards = null) {
  if (!str || !str.includes('⟦')) return str
  const codeRegions = []
  if (guards?.code) {
    for (const m of str.matchAll(guards.code)) {
      codeRegions.push([m.index, m.index + m[0].length])
    }
  }
  const inCode = (i) => codeRegions.some(([a, b]) => i >= a && i < b)
  const matches = []
  for (const m of str.matchAll(new RegExp(INLINE_SENTINEL_SRC, 'g'))) {
    const close = m[1] === '/'
    const hex = m[3] || null
    const valid = (close ? !hex : (m[2] === 'U' ? !hex : !!hex)) && !inCode(m.index)
    matches.push({ index: m.index, len: m[0].length, close, kind: m[2], hex, valid, use: false })
  }
  const stack = []
  for (const t of matches) {
    if (!t.valid) continue
    if (!t.close) { stack.push(t); continue }
    const top = stack[stack.length - 1]
    if (top && top.kind === t.kind) {
      stack.pop()
      const between = str.slice(top.index + top.len, t.index)
      if (guards?.block && guards.block.test(between)) continue
      top.use = t.use = true
    }
    // close without a matching open on top: stays literal, stack untouched
  }
  if (!matches.some((t) => t.use)) return str
  let out = ''
  let last = 0
  for (const t of matches) {
    if (!t.use) continue
    out += str.slice(last, t.index)
    if (t.close) out += t.kind === 'U' ? tags.uClose : tags.colorClose
    else out += t.kind === 'U' ? tags.uOpen : tags.colorOpen(t.hex)
    last = t.index + t.len
  }
  return out + str.slice(last)
}

const HTML_SENTINEL_TAGS = {
  uOpen: '<u>',
  uClose: '</u>',
  colorOpen: (hex) => `<span style="color:${hex}">`, // hex regex-validated
  colorClose: '</span>',
}

const HTML_SENTINEL_GUARDS = { code: CODE_REGION_RE, block: BLOCK_TAG_RE }

const applyInlineSentinels = (html) => replaceInlineSentinels(html, HTML_SENTINEL_TAGS, HTML_SENTINEL_GUARDS)

/**
 * Parse a sentinel table run starting at lines[start] (which must be ⟦TABLE⟧).
 * Returns { rows: [{ cells: [{ header, align, lines }] }], next } on success,
 * or null when the run is malformed (unclosed, mismatched close, unexpected
 * content between markers, nested table/⟦IMG⟧ inside a cell, empty table/row).
 */
export function parseTableRun(lines, start) {
  const arr = lines || []
  if (!TBL_OPEN_RE.test(arr[start] ?? '')) return null
  const rows = []
  let row = null   // cells of the open row, or null between rows
  let cell = null  // { header, align, lines } while inside a cell
  for (let i = start + 1; i < arr.length; i += 1) {
    const line = arr[i]
    if (typeof line !== 'string') return null
    if (cell) {
      const close = line.match(CLOSE_RE)
      if (close && (close[1] === 'TH' || close[1] === 'TD')) {
        if ((cell.header ? 'TH' : 'TD') !== close[1]) return null
        row.push(cell)
        cell = null
        continue
      }
      // Any other marker inside a cell (nested table, stray open/close,
      // illustration) has no representation — the whole run is malformed.
      if (TABLE_MARKER_RE.test(line) || IMG_MARKER_RE.test(line)) return null
      cell.lines.push(line)
      continue
    }
    if (row) {
      const open = line.match(CELL_OPEN_RE)
      if (open) {
        cell = { header: open[1] === 'TH', align: open[2] || null, lines: [] }
        continue
      }
      if (CLOSE_RE.test(line) && line.match(CLOSE_RE)[1] === 'TR') {
        if (!row.length) return null
        rows.push({ cells: row })
        row = null
        continue
      }
      return null
    }
    if (TR_OPEN_RE.test(line)) { row = []; continue }
    if (CLOSE_RE.test(line) && line.match(CLOSE_RE)[1] === 'TABLE') {
      if (row || cell || !rows.length) return null
      return { rows, next: i + 1 }
    }
    return null
  }
  return null // EOF before ⟦/TABLE⟧
}

const md = new MarkdownIt({
  html: false,        // never trust raw HTML in content
  linkify: true,
  breaks: true,       // single newline within a block → <br>
  typographer: false,
})
// Disable Markdown image syntax — illustrations ride as ⟦IMG⟧ markers, not ![]().
md.disable(['image'])
// linkify happily swallows ⟦⟧ into a URL's tail, so a bare URL flush against
// an inline sentinel (⟦U⟧https://x⟦/U⟧) would link to a mangled href and break
// the write editor's round-trip. Reject any URL containing marker brackets —
// the URL stays plain (styled) text instead. Sentinel-free URLs unaffected.
const defaultValidateLink = md.validateLink
// normalizeLink percent-encodes before validateLink runs — match both forms.
const SENTINEL_IN_URL_RE = /⟦|⟧|%e2%9f%a[67]/i
md.validateLink = (url) => defaultValidateLink(url) && !SENTINEL_IN_URL_RE.test(url)

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

/**
 * Parse a Markdown document into the raw markdown-it token stream, using the
 * exact same parser instance/config the Reader renders with. Consumed by
 * writeMarkdown.js (the WYSIWYG bridge) so parse behavior can never diverge
 * from render behavior.
 */
export function parseMarkdownTokens(source) {
  return md.parse(source || '', {})
}

/** Render a Markdown document (block-level) to sanitized HTML. */
export function renderBlock(source) {
  if (!source || !source.trim()) return ''
  return applyInlineSentinels(DOMPurify.sanitize(md.render(source), PURIFY_CONFIG))
}

/** Render a single line as inline-only Markdown (bold/italic/code/links). */
export function renderInline(source) {
  if (!source || !source.trim()) return ''
  return applyInlineSentinels(DOMPurify.sanitize(md.renderInline(source), PURIFY_CONFIG))
}

/**
 * Render a sentinel-table segment ({ rows }) to sanitized HTML. Cell interiors
 * are full block-level Markdown rendered by the same md instance (html:false,
 * so raw HTML in content is entity-escaped before assembly); one sanitize pass
 * over the finished table keeps the link-hardening hook applied throughout.
 * Leading all-header rows go in <thead>; <tbody> is omitted when empty
 * (parity with how markdown-it renders header-only pipe tables).
 */
export function renderTable(seg) {
  const cellHtml = (cell) => {
    const tag = cell.header ? 'th' : 'td'
    const align = cell.align ? ` align="${cell.align}"` : ''
    return `<${tag}${align}>${md.render(cell.lines.join('\n'))}</${tag}>`
  }
  const rowHtml = (row) => `<tr>${row.cells.map(cellHtml).join('')}</tr>`
  const headRows = []
  const bodyRows = []
  for (const row of seg.rows || []) {
    const isHead = row.cells.length && row.cells.every((c) => c.header)
    if (isHead && !bodyRows.length) headRows.push(row)
    else bodyRows.push(row)
  }
  const html = '<table>' +
    (headRows.length ? `<thead>${headRows.map(rowHtml).join('')}</thead>` : '') +
    (bodyRows.length ? `<tbody>${bodyRows.map(rowHtml).join('')}</tbody>` : '') +
    '</table>'
  return applyInlineSentinels(DOMPurify.sanitize(html, PURIFY_CONFIG))
}

/** Render any splitSegments() segment except images (those need URLs). */
export function renderSegment(seg) {
  if (seg.type === 'table') return renderTable(seg)
  return renderBlock(seg.md)
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
 *   { type: 'table', rows }                           — a sentinel table
 * Text runs are joined with '\n' so the Markdown block model applies.
 * A malformed ⟦TABLE⟧ run downgrades to literal text (never loses data) and
 * flags its text segment `tableError: true` so the write editor can refuse
 * to open instead of silently keeping the broken markers.
 */
export function splitSegments(lines) {
  const arr = lines || []
  const segments = []
  let run = []
  let runError = false
  const flush = () => {
    if (run.length) {
      const seg = { type: 'text', md: run.join('\n') }
      if (runError) seg.tableError = true
      segments.push(seg)
      run = []
    }
    runError = false
  }
  let i = 0
  while (i < arr.length) {
    const line = arr[i]
    const id = markerId(line)
    if (id) {
      flush()
      segments.push({ type: 'img', id })
      i += 1
      continue
    }
    if (typeof line === 'string' && TBL_OPEN_RE.test(line)) {
      const table = parseTableRun(arr, i)
      if (table) {
        flush()
        segments.push({ type: 'table', rows: table.rows })
        i = table.next
        continue
      }
      runError = true // only ⟦TABLE⟧ opens a run; orphan markers ride as text
    }
    run.push(line)
    i += 1
  }
  flush()
  return segments
}
