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
import { parseMarkdownTokens, splitSegments, markerId, TABLE_MARKER_RE, INLINE_SENTINEL_RE } from './chapterMarkdown'

// Fixed nesting order for mark delimiters: outermost first. textStyle (color)
// and underline sit outside the emphasis family: their ⟦⟧ markers have no
// flanking rules, so keeping them outer minimizes emphasis-adjacency cases.
const MARK_ORDER = { link: 0, textStyle: 1, underline: 2, bold: 3, italic: 4, strike: 5, code: 6 }

// Foreground color canonical form: lowercase #rrggbb. Accepts #rgb and
// rgb()/rgba() (editor pickers emit hex; pasted HTML often carries rgb()).
// Anything else has no canonical form → the mark is dropped.
function normalizeColor(value) {
  if (!value) return null
  let v = String(value).trim().toLowerCase()
  const rgb = v.match(/^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/)
  if (rgb) {
    v = '#' + [rgb[1], rgb[2], rgb[3]]
      .map((n) => Math.min(255, +n).toString(16).padStart(2, '0')).join('')
  }
  const short = v.match(/^#([0-9a-f])([0-9a-f])([0-9a-f])$/)
  if (short) v = `#${short[1]}${short[1]}${short[2]}${short[2]}${short[3]}${short[3]}`
  return /^#[0-9a-f]{6}$/.test(v) ? v : null
}

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
    } else if (seg.type === 'table') {
      content.push(tableSegmentToNode(seg, unsupported))
    // Blank check with markdown-it's semantics (space/tab-only lines), NOT
    // String.trim(): JS also trims U+2028/U+2029/FEFF etc., which markdown-it
    // treats as visible text — trim() here would drop segments the parser
    // would keep, breaking round-trip symmetry.
    } else if (/[^ \t\n]/.test(seg.md)) {
      // A malformed ⟦TABLE⟧ run rides through as literal text; refuse to edit
      // (saving from the WYSIWYG view could silently keep the broken markers).
      if (seg.tableError) unsupported.push('table:malformed')
      content.push(...tokensToBlocks(parseMarkdownTokens(seg.md), unsupported))
    }
  }
  if (!content.length) content.push({ type: 'paragraph' })
  return { doc: { type: 'doc', content }, unsupported: [...new Set(unsupported)] }
}

// Block types a table cell may contain. Everything else has no sentinel-cell
// form we're willing to support (headings/code/hr read badly in a cell; nested
// tables and illustrations are grammar-level malformed).
const CELL_BLOCK_TYPES = new Set(['paragraph', 'bulletList', 'orderedList', 'listItem', 'blockquote'])

function validateCellBlocks(blocks, report) {
  for (const b of blocks || []) {
    if (!CELL_BLOCK_TYPES.has(b.type)) {
      report(`table-cell:${b.type}`)
      continue
    }
    if (b.type !== 'paragraph') validateCellBlocks(b.content, report)
  }
}

// Sentinel-table segment ({rows: [{cells: [{header, align, lines}]}]}) →
// TipTap table node. Cell interiors are ordinary markdown lines parsed by the
// shared machinery, so cells hold full block content (the whole point of the
// sentinel format — pipe tables can't express it).
function tableSegmentToNode(seg, unsupported) {
  return {
    type: 'table',
    content: seg.rows.map((row) => ({
      type: 'tableRow',
      content: row.cells.map((cell) => {
        const md = cell.lines.join('\n')
        const blocks = /[^ \t\n]/.test(md)
          ? tokensToBlocks(parseMarkdownTokens(md), unsupported)
          : []
        validateCellBlocks(blocks, (w) => unsupported.push(w))
        return {
          type: cell.header ? 'tableHeader' : 'tableCell',
          attrs: { align: cell.align || null },
          content: blocks.length ? blocks : [{ type: 'paragraph' }],
        }
      }),
    })),
  }
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

// Pre-scan an inline run's text tokens for ⟦U⟧/⟦COLOR:#hex⟧ markers and
// stack-match them into pairs (same validity + misnesting rules as the
// renderer's replaceInlineSentinels, so parse ≡ render). Returns a map of
// token index → matched markers in order. Unmatched/invalid markers are NOT
// in the map — they stay literal text (and block the save via the
// sentinel:literal warning on serialize).
function matchInlineSentinels(children) {
  const occ = []
  children.forEach((tok, ti) => {
    if (tok.type !== 'text') return
    for (const m of (tok.content || '').matchAll(new RegExp(INLINE_SENTINEL_RE.source, 'g'))) {
      const close = m[1] === '/'
      const hex = m[3] || null
      occ.push({
        ti, index: m.index, len: m[0].length, close, kind: m[2], hex,
        valid: close ? !hex : (m[2] === 'U' ? !hex : !!hex), use: false,
      })
    }
  })
  const stack = []
  for (const t of occ) {
    if (!t.valid) continue
    if (!t.close) { stack.push(t); continue }
    const top = stack[stack.length - 1]
    if (top && top.kind === t.kind) {
      stack.pop()
      top.use = t.use = true
    }
  }
  const byToken = new Map()
  for (const t of occ) {
    if (!t.use) continue
    if (!byToken.has(t.ti)) byToken.set(t.ti, [])
    byToken.get(t.ti).push(t)
  }
  return byToken
}

function inlineToNodes(children, unsupported) {
  const nodes = []
  const active = [] // open mark stack
  const sentinels = matchInlineSentinels(children)
  let underlineDepth = 0
  const colorStack = []
  const customMarks = () => [
    ...(colorStack.length ? [{ type: 'textStyle', attrs: { color: colorStack[colorStack.length - 1] } }] : []),
    ...(underlineDepth > 0 ? [{ type: 'underline' }] : []),
  ]
  const pushText = (text, extraMarks) => {
    if (!text) return
    const marks = [...customMarks(), ...active, ...(extraMarks || [])].map((m) => ({ ...m }))
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

  children.forEach((tok, ti) => {
    switch (tok.type) {
      case 'text': {
        const markers = sentinels.get(ti)
        if (!markers) { pushText(tok.content); break }
        let pos = 0
        for (const t of markers) {
          pushText(tok.content.slice(pos, t.index))
          if (t.kind === 'U') underlineDepth += t.close ? -1 : 1
          else if (t.close) colorStack.pop()
          else colorStack.push(t.hex)
          pos = t.index + t.len
        }
        pushText(tok.content.slice(pos))
        break
      }
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
  })
  return nodes
}

// ---------------------------------------------------------------------------
// Canonical form (shared by comparison + serialization)
// ---------------------------------------------------------------------------

// Returns null for marks with no canonical form (textStyle without a valid
// color carries no information) — callers filter those out.
function canonMark(mark) {
  if (mark.type === 'link') return { type: 'link', attrs: { href: mark.attrs?.href || '' } }
  if (mark.type === 'textStyle') {
    const color = normalizeColor(mark.attrs?.color)
    return color ? { type: 'textStyle', attrs: { color } } : null
  }
  return { type: mark.type }
}

function sortMarks(marks) {
  return [...marks].sort((a, b) =>
    (MARK_ORDER[a.type] ?? 9) - (MARK_ORDER[b.type] ?? 9) ||
    (a.type < b.type ? -1 : a.type > b.type ? 1 : 0))
}

function marksEqual(a, b) {
  if (a.length !== b.length) return false
  return a.every((m, i) => m.type === b[i].type &&
    (m.attrs?.href || '') === (b[i].attrs?.href || '') &&
    (m.attrs?.color || '') === (b[i].attrs?.color || ''))
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
    const marks = sortMarks((node.marks || []).map(canonMark).filter(Boolean))
    // Inside code spans U+2028/29 must become spaces: markdown-it's pad-strip
    // regex (/^ (.+) $/) can't match across them (JS line terminators), so
    // they'd break the code-span round-trip; the parser itself already maps
    // \n inside code spans to a space.
    const text = marks.some((m) => m.type === 'code')
      ? node.text.replace(/[\u2028\u2029]/g, ' ')
      : node.text
    // Literal line terminators inside a text node reparse as line breaks —
    // canonicalize them to segment (hard-break) boundaries up front.
    const parts = text.split(/\r\n?|\n/)
    parts.forEach((part, idx) => {
      if (idx) segments.push([])
      if (part) segments[segments.length - 1].push({ text: part, marks })
    })
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

// Matches markdown-it's isWhiteSpace (inline set — line breaks can't occur in
// inline text): a mark edge or line edge on any of these, incl. U+00A0, won't
// survive reparse, so the canonical form must treat them as whitespace too.
// eslint-disable-next-line no-control-regex -- \v \f are in markdown-it's whitespace set
const isWs = (ch) => /[ \t\x0B\x0C\u00A0\u1680\u2000-\u200A\u202F\u205F\u3000]/.test(ch)
// markdown-it's paragraph/heading rules trim block content with JS
// String.trim(), whose set is wider than inline whitespace (adds U+2028/29
// line separators and the BOM). Unmarked chars in this set at a line edge
// don't survive reparse.
const isEdgeTrim = (ch) => isWs(ch) || ch === '\u2028' || ch === '\u2029' || ch === '\uFEFF'
// CommonMark 0.31 "Unicode punctuation" for the flanking rules: P and S
// general categories (markdown-it's ASCII punct set is a subset).
const isPunct = (ch) => /[\p{P}\p{S}]/u.test(ch)
const isWordish = (ch) => !isWs(ch) && !isPunct(ch)
// Marks whose ranges markdown cannot open/close against whitespace.
const SHRINKABLE = new Set(['bold', 'italic', 'strike'])
const markKey = (m) => (
  m.type === 'link' ? `link:${m.attrs?.href || ''}`
    : m.type === 'textStyle' ? `textStyle:${m.attrs?.color || ''}`
      : m.type)

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

  // Shrink emphasis ranges off characters the flanking rules would reject,
  // to a global fixpoint (shrinking one mark can move another's boundaries):
  //  - whitespace edges never parse ("*b *");
  //  - an emphasis delimiter can't OPEN before punctuation when a word char
  //    precedes it ("b*#x*"), nor CLOSE after punctuation when a word char
  //    follows ("*x!*s") — CommonMark left/right-flanking. Any material
  //    serialization inserts between neighbors (escapes, nested delimiters)
  //    is itself punctuation, which only relaxes flanking, so judging on raw
  //    chars errs safe;
  //  - overlapping (non-nested) mark ranges are SPLIT at each other's
  //    boundaries when serialized, and each split piece re-opens/closes a
  //    delimiter there — whitespace adjacent to an interior split point is
  //    fatal the same way an edge is (the neighbor across the split is a
  //    delimiter, i.e. punctuation, so only the whitespace rule applies).
  // Drop autolink marks FIRST: linkify recreates them at render time, so
  // they're not canonical — and if they survived into the emphasis pass
  // below, their range boundaries would trigger interior-split expulsion
  // that the linkless side of the comparison never sees.
  for (const [key, mark] of markByKey) {
    if (mark.type !== 'link') continue
    let i = 0
    while (i < chars.length) {
      if (!chars[i].keys.has(key)) { i += 1; continue }
      let j = i
      while (j < chars.length && chars[j].keys.has(key)) j += 1
      const rangeText = chars.slice(i, j).map((c) => c.ch).join('')
      if (isAutoLink(mark.attrs?.href || '', rangeText)) {
        for (let k = i; k < j; k += 1) chars[k].keys.delete(key)
      }
      i = j
    }
  }

  let changedAny = true
  while (changedAny) {
    changedAny = false
    for (const [key, mark] of markByKey) {
      let i = 0
      while (i < chars.length) {
        if (!chars[i].keys.has(key)) { i += 1; continue }
        let j = i
        while (j < chars.length && chars[j].keys.has(key)) j += 1
        if (SHRINKABLE.has(mark.type)) {
          const drop = (k) => { chars[k].keys.delete(key); changedAny = true }
          // The serialized char adjacent to this mark's delimiter is punct
          // not only when the raw edge char is punct, but also when another
          // mark's range starts/ends at the edge — its delimiter (punct) is
          // emitted between our delimiter and the text. Same-character
          // delimiters (bold+italic, both '*'/'_') merge into ONE run that
          // the parser analyzes whole, so only different-char delimiters
          // (~~, `, [ ]) count.
          const DELIM_CHAR = { bold: '*', italic: '*', strike: '~', code: '`', link: '[', underline: '⟦', textStyle: '⟦' }
          const myDelim = DELIM_CHAR[mark.type]
          const otherDelim = (k2) => DELIM_CHAR[k2.split(':')[0]] !== myDelim
          const delimBefore = (idx) => [...chars[idx].keys].some((k2) =>
            k2 !== key && otherDelim(k2) && (idx === 0 || !chars[idx - 1].keys.has(k2)))
          const delimAfter = (idx) => [...chars[idx].keys].some((k2) =>
            k2 !== key && otherDelim(k2) && (idx + 1 >= chars.length || !chars[idx + 1].keys.has(k2)))
          let a = i, b = j
          let edge = true
          while (edge) {
            edge = false
            while (a < b && isWs(chars[a].ch)) { drop(a); a += 1; edge = true }
            while (b > a && isWs(chars[b - 1].ch)) { drop(b - 1); b -= 1; edge = true }
            if (a < b && a > 0 && isWordish(chars[a - 1].ch) &&
                (isPunct(chars[a].ch) || delimBefore(a))) {
              drop(a); a += 1; edge = true
            }
            if (b > a && b < chars.length && isWordish(chars[b].ch) &&
                (isPunct(chars[b - 1].ch) || delimAfter(b - 1))) {
              drop(b - 1); b -= 1; edge = true
            }
          }
          // A mark k2 whose range PROPERLY overlaps ours (starts inside and
          // ends beyond, or vice versa) forces the serializer to close and
          // reopen one of the wraps at the crossing — whitespace next to that
          // interior split point is as fatal as at an outer edge. Nested
          // ranges (fully inside ours) do NOT split us and are exempt.
          for (let p = a + 1; p < b; p += 1) {
            let splits = false
            for (const k2 of new Set([...chars[p - 1].keys, ...chars[p].keys])) {
              if (k2 === key || chars[p - 1].keys.has(k2) === chars[p].keys.has(k2)) continue
              if (chars[p].keys.has(k2)) {
                // k2 starts at p — splits us if it runs past our end.
                let e = p
                while (e < chars.length && chars[e].keys.has(k2)) e += 1
                if (e > b) { splits = true; break }
              } else {
                // k2 ends at p — splits us if it started before our start.
                let s = p - 1
                while (s >= 0 && chars[s].keys.has(k2)) s -= 1
                if (s + 1 < a) { splits = true; break }
              }
            }
            if (!splits) continue
            for (let q = p - 1; q >= a && isWs(chars[q].ch) && chars[q].keys.has(key); q -= 1) drop(q)
            for (let q = p; q < b && isWs(chars[q].ch) && chars[q].keys.has(key); q += 1) drop(q)
          }
        }
        i = j
      }
    }
  }

  // Trim line-edge whitespace that no mark protects (JS-trim set — matches
  // markdown-it's block-content .trim()).
  let start = 0
  let end = chars.length
  while (start < end && isEdgeTrim(chars[start].ch) && chars[start].keys.size === 0) start += 1
  while (end > start && isEdgeTrim(chars[end - 1].ch) && chars[end - 1].keys.size === 0) end -= 1

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
        // elsewhere. A fully headerless table (all cells plain) has exactly
        // one representable form — first row as header — so canonicalize it
        // to that transparently. Anything else (header cells scattered in
        // later rows, mixed rows) has no pipe-table representation.
        const allPlain = rows.every((r) => r.content.every((c) => c.type === 'tableCell'))
        if (allPlain) {
          rows[0].content = rows[0].content.map((c) => ({ ...c, type: 'tableHeader' }))
        }
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

// Table cells hold block content (paragraphs with hard breaks, lists,
// blockquotes) — normalized recursively like any block run. Types outside the
// allowed cell set warn (blocks the save), as do merged cells (no
// sentinel/pipe form for either).
function normalizeTableCell(cell, warnings) {
  if ((cell.attrs?.colspan || 1) > 1 || (cell.attrs?.rowspan || 1) > 1) {
    warnings.push('table:merged-cells')
  }
  validateCellBlocks(cell.content, (w) => warnings.push(w))
  const content = normalizeBlocks(cell.content || [], warnings)
  return {
    type: cell.type,
    attrs: { align: cell.attrs?.align ?? null },
    content: content.length ? content : [{ type: 'paragraph', content: [] }],
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
      return segmentLines(node.content, warnings).map(guardLine).map((l) => {
        // A paragraph line that IS a table marker would open/derail a
        // sentinel run on reparse; ⟦⟧ has no escaped form, so block the save.
        if (TABLE_MARKER_RE.test(l)) warnings.push('sentinel:literal')
        return l
      })
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

// A cell a pipe table can hold: exactly one paragraph, no hard breaks.
function isSimpleCell(cell) {
  return (cell.content || []).length === 1 &&
    cell.content[0].type === 'paragraph' &&
    !(cell.content[0].content || []).some((n) => n.type === 'hardBreak')
}

// All-simple tables keep the pipe form (corpus byte-parity); any cell with
// breaks, extra paragraphs, lists, or quotes switches the whole table to the
// sentinel ⟦TABLE⟧ form. parse(pipe) can only yield simple cells and
// parse(sentinel) reproduces rich structure, so the classification is stable
// across serialize→reparse — it flips only when the user genuinely adds or
// removes rich content.
function serializeTable(node, warnings) {
  const simple = node.content.every((row) => row.content.every(isSimpleCell))
  return simple ? serializePipeTable(node, warnings) : serializeRichTable(node, warnings)
}

function serializePipeTable(node, warnings) {
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

function serializeRichTable(node, warnings) {
  const lines = ['⟦TABLE⟧']
  node.content.forEach((row, ri) => {
    lines.push('⟦TR⟧')
    for (const cell of row.content) {
      const header = cell.type === 'tableHeader'
      // Alignment is canonicalized per-column off the header row, so it is
      // only ever emitted there — body cells reacquire it on reparse.
      const align = ri === 0 && cell.attrs?.align ? `:${cell.attrs.align}` : ''
      lines.push(`⟦${header ? 'TH' : 'TD'}${align}⟧`)
      let inner = []
      for (const child of cell.content || []) {
        const childLines = serializeBlock(child, warnings)
        if (!childLines.length) continue
        if (inner.length) inner.push('')
        inner.push(...childLines)
      }
      if (!inner.some((l) => l)) inner = [] // empty cell: nothing between markers
      for (const l of inner) {
        // A cell line that IS a marker would terminate the cell early (or
        // malform the run) on reparse — no escaped form exists, so block.
        if (TABLE_MARKER_RE.test(l) || markerId(l) !== null) {
          warnings.push('table-cell:marker-literal')
        }
      }
      lines.push(...inner, `⟦/${header ? 'TH' : 'TD'}⟧`)
    }
    lines.push('⟦/TR⟧')
  })
  lines.push('⟦/TABLE⟧')
  return lines
}

function serializeList(items, start, warnings) {
  const lines = []
  items.forEach((item, idx) => {
    const marker = start != null ? `${start + idx}. ` : '- '
    // Child indent: CommonMark only needs the marker width (2 for "- "), but
    // python-markdown (EPUB/WordPress parity) requires 4 spaces to nest —
    // marker-width indents flatten nested lists there. 4 parses identically
    // in markdown-it (still below the content-column+4 code threshold), so
    // serialize the python-compatible form. Wide markers ("10. ") keep
    // their full content column.
    const indent = ' '.repeat(Math.max(4, marker.length))
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
    // Literal ⟦U⟧/⟦COLOR⟧ text (typed or pasted) would toggle formatting on
    // reparse; ⟦⟧ has no escaped form, so block the save. Code spans are
    // exempt — their content stays literal through parse and render.
    if (!marks.some((m) => m.type === 'code') && INLINE_SENTINEL_RE.test(node.text)) {
      warnings.push('sentinel:literal')
    }
    if (!marks.length) {
      out += escapeInline(node.text)
      i += 1
      continue
    }
    // Pick the outer wrap: the mark whose contiguous run over the following
    // nodes is longest (ties broken by canonical MARK_ORDER). Wrapping a
    // shorter-run mark first would split the longer range into fragments,
    // producing adjacent delimiter runs ("***h****#d*") that CommonMark's
    // rule-of-three pairs differently than intended.
    const runLen = (m) => {
      let k = i
      while (k < nodes.length && nodes[k].type === 'text' &&
             (nodes[k].marks || []).some((mm) => marksEqual([mm], [m]))) k += 1
      return k - i
    }
    // code is always the innermost wrap (serializeCode is terminal — picking
    // it as outer would silently drop the node's other marks), so it only
    // competes when it's the node's sole mark.
    const candidates = sortMarks(marks)
    const nonCode = candidates.filter((m) => m.type !== 'code')
    const mark = (nonCode.length ? nonCode : candidates).reduce((best, m) =>
      (runLen(m) > runLen(best) ? m : best))
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
    const nextChar = (nodes[j] && nodes[j].type === 'text' && nodes[j].text[0]) || ''
    out += wrapMark(mark, inner, warnings, out.slice(-1), nextChar)
    i = j
  }
  return out
}

function wrapMark(mark, inner, warnings, prevChar = '', nextChar = '') {
  // An emphasis wrap directly after a '*' delimiter would merge into one
  // ambiguous run ("**a***b*" → "*****"-style mispairing). Switch to the '_'
  // family there — but only when no word char follows, since '_' can't close
  // intraword. prevChar '*' is punctuation, so '_' can always open after it.
  const underscore = prevChar === '*' && (!nextChar || !isWordish(nextChar))
  switch (mark.type) {
    case 'bold': return underscore ? `__${inner}__` : `**${inner}**`
    case 'italic': return underscore ? `_${inner}_` : `*${inner}*`
    case 'strike': return `~~${inner}~~`
    case 'underline': return `⟦U⟧${inner}⟦/U⟧`
    case 'textStyle': return `⟦COLOR:${mark.attrs?.color}⟧${inner}⟦/COLOR⟧`
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
    // Escape every literal backslash: escaping is span-local, so a trailing
    // backslash can't know whether serialization appends escapable punctuation
    // (or a mark delimiter) right after it.
    .replace(/\\/g, '\\\\')
    .replace(/([*_`])/g, '\\$1')
    // Escape every tilde: a bare ~ adjacent to a ~~ strike delimiter would
    // merge into a ~~~ run that markdown-it refuses to reparse as strike.
    .replace(/~/g, '\\~')
    // [ opens links/refs and ] closes a link label early — but leave footnote
    // refs [n] untouched (byte-exact)
    .replace(/\[(?!\d+\])/g, '\\[')
    .replace(/(?<!\[\d+)\]/g, '\\]')
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
  const canonDoc = normalizeDoc(doc)
  const canonRe = normalizeDoc(reparsed)
  const ok = warnings.length === 0 && unsupported.length === 0 &&
    JSON.stringify(canonRe) === JSON.stringify(canonDoc)
  let mismatch = null
  if (!ok && !warnings.length && !unsupported.length) {
    // Pinpoint the first block whose canonical form diverges, so the save
    // error can name the paragraph instead of just "somewhere".
    const n = Math.max(canonDoc.content.length, canonRe.content.length)
    for (let i = 0; i < n; i += 1) {
      if (JSON.stringify(canonDoc.content[i]) !== JSON.stringify(canonRe.content[i])) {
        const blockText = (canonDoc.content[i]?.content || [])
          .map((node) => node.text || '')
          .join('')
        mismatch = { index: i, excerpt: blockText.slice(0, 60) }
        break
      }
    }
  }
  return { ok, lines, warnings: [...new Set(warnings)], unsupported, mismatch }
}
