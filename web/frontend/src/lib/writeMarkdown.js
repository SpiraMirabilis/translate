// Markdown ⇄ TipTap document bridge for the write editor (original works).
//
// Chapter content is stored as a JSON array of Markdown lines: "" separates
// paragraphs, consecutive non-empty lines are one paragraph with hard breaks
// (the Reader renders with markdown-it breaks:true), and ⟦IMG:id⟧ lines mark
// illustrations. This module converts that storage format to a TipTap JSON
// document and back.
//
// Parsing reuses the Reader's own markdown-it instance (parseMarkdownTokens),
// so what the editor loads can never diverge from what the Reader renders.
// Serialization is hand-rolled for the small supported node set, with minimal
// escaping — only where unescaped text would change meaning on reparse.
//
// The safety valve is roundTrip(): serialize → reparse → compare canonical
// forms. Every save runs it; a mismatch blocks the save loudly instead of
// silently corrupting content. normalizeDoc() defines the canonical form both
// sides are reduced to (mark order, merged text nodes, expelled edge
// whitespace, paragraph splits at double breaks, …).
import { parseMarkdownTokens, splitSegments } from './chapterMarkdown'

// Fixed nesting order for mark delimiters: outermost first.
const MARK_ORDER = { link: 0, bold: 1, italic: 2, strike: 3, code: 4 }

// ---------------------------------------------------------------------------
// lines → TipTap doc
// ---------------------------------------------------------------------------

/**
 * Convert a stored content line array into a TipTap JSON doc.
 * Returns { doc, unsupported } — `unsupported` lists markdown token types the
 * write editor has no node for (e.g. tables); callers should refuse to edit
 * when it's non-empty, since saving would drop that content.
 */
export function linesToDoc(lines) {
  const unsupported = []
  const content = []
  for (const seg of splitSegments(lines || [])) {
    if (seg.type === 'img') {
      content.push({ type: 'illustration', attrs: { id: seg.id } })
    } else if (seg.md.trim()) {
      content.push(...tokensToBlocks(parseMarkdownTokens(seg.md), unsupported))
    }
  }
  if (!content.length) content.push({ type: 'paragraph' })
  return { doc: { type: 'doc', content }, unsupported: [...new Set(unsupported)] }
}

// markdown-it marks column alignment on th/td tokens as an inline style.
function alignFromToken(tok) {
  const style = tok.attrGet('style') || ''
  const m = style.match(/text-align:\s*(left|center|right)/)
  return m ? m[1] : null
}

function tokensToBlocks(tokens, unsupported) {
  const root = { type: 'root', content: [] }
  const stack = [root]
  const top = () => stack[stack.length - 1]
  const append = (node) => top().content.push(node)
  const open = (node) => { node.content = node.content || []; append(node); stack.push(node) }
  const close = () => { if (stack.length > 1) stack.pop() }

  for (const tok of tokens) {
    switch (tok.type) {
      case 'paragraph_open': open({ type: 'paragraph' }); break
      case 'paragraph_close': close(); break
      case 'heading_open':
        open({ type: 'heading', attrs: { level: Number(tok.tag.slice(1)) || 1 } }); break
      case 'heading_close': close(); break
      case 'blockquote_open': open({ type: 'blockquote' }); break
      case 'blockquote_close': close(); break
      case 'bullet_list_open': open({ type: 'bulletList' }); break
      case 'bullet_list_close': close(); break
      case 'ordered_list_open':
        open({ type: 'orderedList', attrs: { start: Number(tok.attrGet('start')) || 1 } }); break
      case 'ordered_list_close': close(); break
      case 'list_item_open': open({ type: 'listItem' }); break
      case 'list_item_close': close(); break
      case 'hr': append({ type: 'horizontalRule' }); break
      case 'fence':
      case 'code_block': {
        const text = (tok.content || '').replace(/\n$/, '')
        append({
          type: 'codeBlock',
          attrs: { language: (tok.info || '').trim() || null },
          content: text ? [{ type: 'text', text }] : [],
        })
        break
      }
      case 'inline':
        top().content.push(...inlineToNodes(tok.children || [], unsupported))
        break
      case 'table_open': open({ type: 'table' }); break
      case 'table_close': close(); break
      case 'thead_open':
      case 'thead_close':
      case 'tbody_open':
      case 'tbody_close':
        break // structural only — header-ness lives on the cell type
      case 'tr_open': open({ type: 'tableRow' }); break
      case 'tr_close': close(); break
      case 'th_open':
      case 'td_open':
        // TipTap cells hold block content: wrap the cell's inline run in a paragraph.
        open({
          type: tok.type === 'th_open' ? 'tableHeader' : 'tableCell',
          attrs: { align: alignFromToken(tok) },
        })
        open({ type: 'paragraph' })
        break
      case 'th_close':
      case 'td_close':
        close()
        close()
        break
      default:
        unsupported.push(tok.type)
    }
  }
  return root.content
}

function inlineToNodes(children, unsupported) {
  const nodes = []
  const active = [] // open mark stack
  const pushText = (text, extraMarks) => {
    if (!text) return
    const marks = [...active, ...(extraMarks || [])].map((m) => ({ ...m }))
    const last = nodes[nodes.length - 1]
    if (last && last.type === 'text' && marksEqual(last.marks || [], marks)) {
      last.text += text
    } else {
      nodes.push(marks.length ? { type: 'text', text, marks } : { type: 'text', text })
    }
  }
  const dropMark = (type) => {
    const idx = active.map((m) => m.type).lastIndexOf(type)
    if (idx !== -1) active.splice(idx, 1)
  }

  for (const tok of children) {
    switch (tok.type) {
      case 'text':
      case 'text_special':
        pushText(tok.content)
        break
      case 'softbreak':
      case 'hardbreak':
        nodes.push({ type: 'hardBreak' })
        break
      case 'code_inline':
        pushText(tok.content, [{ type: 'code' }])
        break
      case 'strong_open': active.push({ type: 'bold' }); break
      case 'strong_close': dropMark('bold'); break
      case 'em_open': active.push({ type: 'italic' }); break
      case 'em_close': dropMark('italic'); break
      case 's_open': active.push({ type: 'strike' }); break
      case 's_close': dropMark('strike'); break
      case 'link_open':
        active.push({ type: 'link', attrs: { href: tok.attrGet('href') || '' } })
        break
      case 'link_close': dropMark('link'); break
      default:
        unsupported.push(tok.type)
    }
  }
  return nodes
}

// ---------------------------------------------------------------------------
// Canonical form (shared by comparison + serialization)
// ---------------------------------------------------------------------------

function canonMark(mark) {
  if (mark.type === 'link') return { type: 'link', attrs: { href: mark.attrs?.href || '' } }
  return { type: mark.type }
}

function sortMarks(marks) {
  return [...marks].sort((a, b) =>
    (MARK_ORDER[a.type] ?? 9) - (MARK_ORDER[b.type] ?? 9) ||
    (a.type < b.type ? -1 : a.type > b.type ? 1 : 0))
}

function marksEqual(a, b) {
  if (a.length !== b.length) return false
  return a.every((m, i) => m.type === b[i].type && (m.attrs?.href || '') === (b[i].attrs?.href || ''))
}

// A link whose href is just the linkified form of its own text carries no
// information: markdown-it's linkify will recreate it at render time. Dropped
// from the canonical form (and serialized as bare text).
function isAutoLink(href, text) {
  return href === text || href === `http://${text}` ||
    href === `https://${text}` || href === `mailto:${text}`
}

/**
 * Reduce inline content to segments (split at hardBreak) of canonical spans
 * {text, marks} — marks sorted, autolinks dropped, adjacent same-marked spans
 * merged, emphasis mark ranges shrunk off edge whitespace (markdown emphasis
 * delimiters can't flank whitespace), segment edges trimmed.
 * Unknown inline nodes ride through as {unknown: type} so mismatches surface.
 */
function toSegments(content) {
  const segments = [[]]
  for (const node of content || []) {
    if (node.type === 'hardBreak') { segments.push([]); continue }
    const seg = segments[segments.length - 1]
    if (node.type !== 'text') { seg.push({ unknown: node.type }); continue }
    const marks = sortMarks((node.marks || []).map(canonMark))
    seg.push({ text: node.text, marks })
  }
  return segments.map(canonicalizeSegment)
}

function mergeSpans(spans) {
  const merged = []
  for (const s of spans) {
    const last = merged[merged.length - 1]
    if (last && !last.unknown && !s.unknown && marksEqual(last.marks, s.marks)) {
      last.text += s.text
    } else {
      merged.push({ ...s })
    }
  }
  return merged.filter((s) => s.unknown || s.text)
}

const isWs = (ch) => ch === ' ' || ch === '\t'
// Marks whose ranges markdown cannot open/close against whitespace.
const SHRINKABLE = new Set(['bold', 'italic', 'strike'])
const markKey = (m) => (m.type === 'link' ? `link:${m.attrs?.href || ''}` : m.type)

/**
 * Char-level canonicalization of one hardBreak-separated segment:
 *   1. shrink each emphasis mark's contiguous ranges off edge whitespace
 *      ("*b *" never reparses as emphasis; "**a *i* b**" keeps its interior
 *      spaces — only range edges matter);
 *   2. drop autolink marks (linkify recreates them at render);
 *   3. trim unmarked whitespace at the segment (= line) edges, which the
 *      block parser strips anyway;
 *   4. rebuild merged spans.
 */
function canonicalizeSegment(spans) {
  spans = mergeSpans(spans)
  if (spans.some((s) => s.unknown)) return spans // surfaced as warnings later
  if (!spans.length) return spans

  const chars = []
  const markByKey = new Map()
  for (const s of spans) {
    for (const m of s.marks) markByKey.set(markKey(m), m)
    for (const ch of s.text) chars.push({ ch, keys: new Set(s.marks.map(markKey)) })
  }

  for (const [key, mark] of markByKey) {
    let i = 0
    while (i < chars.length) {
      if (!chars[i].keys.has(key)) { i += 1; continue }
      let j = i
      while (j < chars.length && chars[j].keys.has(key)) j += 1
      if (SHRINKABLE.has(mark.type)) {
        let a = i, b = j
        while (a < b && isWs(chars[a].ch)) a += 1
        while (b > a && isWs(chars[b - 1].ch)) b -= 1
        for (let k = i; k < a; k += 1) chars[k].keys.delete(key)
        for (let k = b; k < j; k += 1) chars[k].keys.delete(key)
      } else if (mark.type === 'link') {
        const rangeText = chars.slice(i, j).map((c) => c.ch).join('')
        if (isAutoLink(mark.attrs?.href || '', rangeText)) {
          for (let k = i; k < j; k += 1) chars[k].keys.delete(key)
        }
      }
      i = j
    }
  }

  // Trim line-edge whitespace that no mark protects.
  let start = 0
  let end = chars.length
  while (start < end && isWs(chars[start].ch) && chars[start].keys.size === 0) start += 1
  while (end > start && isWs(chars[end - 1].ch) && chars[end - 1].keys.size === 0) end -= 1

  const out = []
  for (let i = start; i < end; i += 1) {
    const marks = sortMarks([...chars[i].keys].map((k) => markByKey.get(k)))
    const last = out[out.length - 1]
    if (last && marksEqual(last.marks, marks)) last.text += chars[i].ch
    else out.push({ text: chars[i].ch, marks })
  }
  return out
}

function spanToNode(s) {
  if (s.unknown) return { type: s.unknown }
  return s.marks.length ? { type: 'text', text: s.text, marks: s.marks } : { type: 'text', text: s.text }
}

/**
 * Canonicalize a TipTap doc for comparison and serialization. Idempotent.
 * `warnings` collects node types the serializer has no representation for.
 */
export function normalizeDoc(doc, warnings = []) {
  return { type: 'doc', content: normalizeBlocks(doc?.content || [], warnings) }
}

function normalizeBlocks(blocks, warnings) {
  const out = []
  for (const b of blocks || []) {
    switch (b.type) {
      case 'paragraph':
        out.push(...normalizeParagraph(b))
        break
      case 'heading': {
        // Headings are single-line: hard breaks collapse to a space.
        const spans = []
        for (const seg of toSegments(b.content)) {
          if (!seg.length) continue
          if (spans.length) spans.push({ text: ' ', marks: [] })
          spans.push(...seg)
        }
        const content = canonicalizeSegment(spans).map(spanToNode)
        if (content.length) {
          out.push({ type: 'heading', attrs: { level: b.attrs?.level || 1 }, content })
        }
        break
      }
      case 'blockquote': {
        const content = normalizeBlocks(b.content, warnings)
        if (content.length) out.push({ type: 'blockquote', content })
        break
      }
      case 'horizontalRule':
        out.push({ type: 'horizontalRule' })
        break
      case 'codeBlock': {
        const text = (b.content || []).map((n) => n.text || '').join('')
        out.push({
          type: 'codeBlock',
          attrs: { language: b.attrs?.language || null },
          content: text ? [{ type: 'text', text }] : [],
        })
        break
      }
      case 'bulletList':
      case 'orderedList': {
        const items = (b.content || [])
          .filter((it) => it.type === 'listItem')
          .map((it) => {
            const content = normalizeBlocks(it.content, warnings)
            return { type: 'listItem', content: content.length ? content : [{ type: 'paragraph', content: [] }] }
          })
        if (items.length) {
          out.push(b.type === 'orderedList'
            ? { type: 'orderedList', attrs: { start: b.attrs?.start ?? 1 }, content: items }
            : { type: 'bulletList', content: items })
        }
        break
      }
      case 'illustration':
        out.push({ type: 'illustration', attrs: { id: b.attrs?.id } })
        break
      case 'table': {
        const rows = (b.content || [])
          .filter((r) => r.type === 'tableRow')
          .map((row) => ({
            type: 'tableRow',
            content: (row.content || [])
              .filter((c) => c.type === 'tableHeader' || c.type === 'tableCell')
              .map((c) => normalizeTableCell(c, warnings)),
          }))
          .filter((r) => r.content.length)
        if (!rows.length) break
        // Markdown requires a header row and can't express header cells
        // elsewhere — anything else has no pipe-table representation.
        const headerFirst = rows[0].content.every((c) => c.type === 'tableHeader') &&
          rows.slice(1).every((r) => r.content.every((c) => c.type === 'tableCell'))
        if (!headerFirst) warnings.push('table:structure')
        // Alignment is a per-column property (the separator row): canonicalize
        // every cell to its header column's alignment so editor-added rows
        // (align null) compare equal to the reparse.
        const colAlign = rows[0].content.map((c) => c.attrs.align)
        for (const row of rows) {
          row.content.forEach((c, i) => { c.attrs = { align: colAlign[i] ?? null } })
        }
        out.push({ type: 'table', content: rows })
        break
      }
      default:
        warnings.push(`node:${b.type}`)
    }
  }
  return out
}

// Markdown table cells are single-line, inline-only: flatten the cell's
// paragraphs (hard breaks and paragraph boundaries become spaces, like
// headings). Merged cells have no pipe-table form — warn, which blocks saves.
function normalizeTableCell(cell, warnings) {
  if ((cell.attrs?.colspan || 1) > 1 || (cell.attrs?.rowspan || 1) > 1) {
    warnings.push('table:merged-cells')
  }
  const spans = []
  for (const child of cell.content || []) {
    if (child.type !== 'paragraph') {
      warnings.push(`table-cell:${child.type}`)
      continue
    }
    for (const seg of toSegments(child.content)) {
      if (!seg.length) continue
      if (spans.length) spans.push({ text: ' ', marks: [] })
      spans.push(...seg)
    }
  }
  const content = canonicalizeSegment(spans).map(spanToNode)
  return {
    type: cell.type,
    attrs: { align: cell.attrs?.align ?? null },
    content: [{ type: 'paragraph', content }],
  }
}

function normalizeParagraph(node) {
  const segments = toSegments(node.content)
  // Interior empty segments (double hard breaks) become paragraph splits —
  // exactly what serialize→reparse produces. Edge empties are dropped.
  const paras = []
  let cur = []
  for (const seg of segments) {
    if (!seg.length) {
      if (cur.length) { paras.push(cur); cur = [] }
      continue
    }
    cur.push(seg)
  }
  if (cur.length) paras.push(cur)
  return paras.map((segs) => ({
    type: 'paragraph',
    content: segs.flatMap((seg, i) => [
      ...(i ? [{ type: 'hardBreak' }] : []),
      ...seg.map(spanToNode),
    ]),
  }))
}

// ---------------------------------------------------------------------------
// TipTap doc → lines
// ---------------------------------------------------------------------------

/**
 * Serialize a TipTap doc to the stored content line array.
 * `warnings` collects node/mark types with no markdown representation —
 * treat any warning as "do not save".
 */
export function docToLines(doc, warnings = []) {
  const norm = normalizeDoc(doc, warnings)
  const lines = []
  for (const block of norm.content) {
    const blockLines = serializeBlock(block, warnings)
    if (!blockLines.length) continue
    if (lines.length) lines.push('')
    lines.push(...blockLines)
  }
  return lines
}

function serializeBlock(node, warnings) {
  switch (node.type) {
    case 'paragraph':
      return segmentLines(node.content, warnings).map(guardLine)
    case 'heading': {
      const inner = segmentLines(node.content, warnings).join(' ')
      return [`${'#'.repeat(node.attrs.level)} ${inner}`.trimEnd()]
    }
    case 'blockquote': {
      const inner = []
      for (const child of node.content) {
        const childLines = serializeBlock(child, warnings)
        if (!childLines.length) continue
        if (inner.length) inner.push('')
        inner.push(...childLines)
      }
      return inner.map((l) => (l ? `> ${l}` : '>'))
    }
    case 'horizontalRule':
      return ['---']
    case 'codeBlock': {
      const text = node.content?.[0]?.text || ''
      const runs = text.match(/`+/g) || []
      const fence = '`'.repeat(Math.max(3, ...runs.map((r) => r.length + 1)))
      return [fence + (node.attrs.language || ''), ...(text ? text.split('\n') : []), fence]
    }
    case 'bulletList':
      return serializeList(node.content, null, warnings)
    case 'orderedList':
      return serializeList(node.content, node.attrs?.start ?? 1, warnings)
    case 'illustration':
      return [`⟦IMG:${node.attrs.id}⟧`]
    case 'table':
      return serializeTable(node, warnings)
    default:
      warnings.push(`node:${node.type}`)
      return []
  }
}

// Separator-cell styles chosen to reproduce the two formats already in the
// corpus byte-for-byte: unaligned system boxes use "| --- |" (padded), the
// chatgroup script's explicit left-align uses "|:---|" (compact).
const SEP_CELL = { left: ':---', center: ' :---: ', right: ' ---: ' }

function serializeTable(node, warnings) {
  const rowLine = (row) =>
    '|' + row.content.map((cell) => {
      const inner = serializeSpans(cell.content[0]?.content || [], warnings)
        // Any unescaped pipe would split the row — escape everywhere,
        // including inside code spans (GFM unescapes \| there too).
        .replace(/(?<!\\)\|/g, '\\|')
      return ` ${inner} `
    }).join('|') + '|'

  const [header, ...body] = node.content
  const sep = '|' + header.content
    .map((c) => SEP_CELL[c.attrs.align] || ' --- ')
    .join('|') + '|'
  return [rowLine(header), sep, ...body.map(rowLine)]
}

function serializeList(items, start, warnings) {
  const lines = []
  items.forEach((item, idx) => {
    const marker = start != null ? `${start + idx}. ` : '- '
    const indent = ' '.repeat(marker.length)
    const itemLines = []
    for (const child of item.content) {
      const childLines = serializeBlock(child, warnings)
      if (!childLines.length) continue
      // A nested list attaches directly under its parent line (tight);
      // a blank line before it would only make the list loose.
      const isNestedList = child.type === 'bulletList' || child.type === 'orderedList'
      if (itemLines.length && !isNestedList) itemLines.push('')
      itemLines.push(...childLines)
    }
    if (!itemLines.length) itemLines.push('')
    itemLines.forEach((l, li) => {
      if (li === 0) lines.push((marker + l).trimEnd())
      else lines.push(l ? indent + l : l)
    })
  })
  return lines
}

/** Inline content → one line per hardBreak-separated segment. */
function segmentLines(content, warnings) {
  const segments = [[]]
  for (const node of content || []) {
    if (node.type === 'hardBreak') segments.push([])
    else segments[segments.length - 1].push(node)
  }
  return segments.map((seg) => serializeSpans(seg, warnings))
}

function serializeSpans(nodes, warnings) {
  let out = ''
  let i = 0
  while (i < nodes.length) {
    const node = nodes[i]
    if (node.type !== 'text') {
      warnings.push(`inline:${node.type}`)
      i += 1
      continue
    }
    const marks = node.marks || []
    if (!marks.length) {
      out += escapeInline(node.text)
      i += 1
      continue
    }
    const mark = sortMarks(marks)[0]
    if (mark.type === 'code') {
      out += serializeCode(node.text)
      i += 1
      continue
    }
    // Extend the run over consecutive text nodes sharing this mark, then
    // recurse with the mark stripped — yields properly nested delimiters.
    let j = i
    while (j < nodes.length && nodes[j].type === 'text' &&
           (nodes[j].marks || []).some((m) => marksEqual([m], [mark]))) j += 1
    const inner = serializeSpans(
      nodes.slice(i, j).map((n) => ({
        ...n,
        marks: (n.marks || []).filter((m) => !marksEqual([m], [mark])),
      })),
      warnings)
    out += wrapMark(mark, inner, warnings)
    i = j
  }
  return out
}

function wrapMark(mark, inner, warnings) {
  switch (mark.type) {
    case 'bold': return `**${inner}**`
    case 'italic': return `*${inner}*`
    case 'strike': return `~~${inner}~~`
    case 'link': {
      const href = mark.attrs?.href || ''
      const dest = /[\s()]/.test(href) ? `<${href}>` : href
      return `[${inner}](${dest})`
    }
    default:
      warnings.push(`mark:${mark.type}`)
      return inner
  }
}

function serializeCode(text) {
  const runs = text.match(/`+/g) || []
  const fence = '`'.repeat(Math.max(1, ...runs.map((r) => r.length + 1)))
  // Pad when the content starts/ends with a backtick (or is all-space):
  // the parser strips one space from each end when both are present.
  const pad = /^`|`$/.test(text) || /^ .* $/.test(text) || text === ' ' ? ' ' : ''
  return `${fence}${pad}${text}${pad}${fence}`
}

// Bare URLs/emails are left unescaped so linkify re-links them cleanly
// (escaping inside a URL would change what linkify matches).
const SKIP_ESCAPE_RE = /\bhttps?:\/\/[^\s<>]+|\bwww\.[^\s<>]+|[^\s<>@[\]`*_~\\]+@[^\s<>]+\.[a-z]{2,}/gi

function escapeInline(text) {
  let out = ''
  let last = 0
  for (const m of text.matchAll(SKIP_ESCAPE_RE)) {
    out += escapeProse(text.slice(last, m.index)) + m[0]
    last = m.index + m[0].length
  }
  return out + escapeProse(text.slice(last))
}

function escapeProse(text) {
  return text
    // A literal backslash before escapable punctuation would eat the next char
    .replace(/\\(?=[!"#$%&'()*+,\-./:;<=>?@[\]\\^_`{|}~])/g, '\\\\')
    .replace(/([*_`])/g, '\\$1')
    // Strikethrough needs a ~~ pair; single tildes stay readable
    .replace(/~(?=~)/g, '\\~')
    // [ opens links/refs — but leave footnote refs [n] untouched (byte-exact)
    .replace(/\[(?!\d+\])/g, '\\[')
    // Literal HTML entities would decode on reparse
    .replace(/&(?=[a-zA-Z][a-zA-Z0-9]{1,31};|#\d{1,8};|#[xX][0-9a-fA-F]{1,8};)/g, '\\&')
    // <scheme:...> / <user@host> would autolink
    .replace(/<(?=[a-zA-Z][a-zA-Z0-9+.-]*:\S|[^\s>]+@[^\s>]+)/g, '\\<')
}

// Escape constructs that would start a different block when this text lands
// at the start of a line. With breaks:true every serialized line is a
// potential block start (lists/headings/quotes interrupt paragraphs, and a
// -/= line under text forms a setext heading).
function guardLine(line) {
  if (!line) return line
  if (/^ {0,3}#{1,6}(\s|$)/.test(line)) return line.replace('#', '\\#')
  if (/^ {0,3}>/.test(line)) return line.replace('>', '\\>')
  if (/^ {0,3}[-+](\s|$)/.test(line)) return line.replace(/[-+]/, (m) => `\\${m}`)
  if (/^ {0,3}\d{1,9}[.)](\s|$)/.test(line)) return line.replace(/[.)]/, (m) => `\\${m}`)
  if (/^ {0,3}(-[ \t]*)+$/.test(line)) return line.replace('-', '\\-')
  if (/^ {0,3}(=[ \t]*)+$/.test(line)) return line.replace('=', '\\=')
  return line
}

// ---------------------------------------------------------------------------
// Round-trip safety check
// ---------------------------------------------------------------------------

/**
 * Serialize a doc and verify the result reparses to the same canonical
 * document. Run before every save; on !ok the save must be blocked.
 * Returns { ok, lines, warnings, unsupported }.
 */
export function roundTrip(doc) {
  const warnings = []
  const lines = docToLines(doc, warnings)
  const { doc: reparsed, unsupported } = linesToDoc(lines)
  const ok = warnings.length === 0 && unsupported.length === 0 &&
    JSON.stringify(normalizeDoc(reparsed)) === JSON.stringify(normalizeDoc(doc))
  return { ok, lines, warnings: [...new Set(warnings)], unsupported }
}
