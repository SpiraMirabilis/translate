// Markdown → XenForo BBCode for forum co-publishing (SpaceBattles /
// SufficientVelocity / QQ all run XenForo 2.2+).
//
// The pipeline reuses the battle-tested pieces: the editor doc is serialized
// with docToLines (round-trip-verified), then parsed with the Reader's own
// markdown-it instance — so BBCode output can never diverge from what the
// Reader renders. The token walk below only needs to handle the node set
// writeMarkdown.js can produce.
import { parseMarkdownTokens, splitSegments, replaceInlineSentinels } from './chapterMarkdown'
import { docToLines } from './writeMarkdown'

// ⟦U⟧/⟦COLOR:#hex⟧ inline sentinels → XenForo tags. Markers ride through the
// markdown token walk as literal text; balanced pairs are swapped at the end
// (same pairing rules as the Reader, so output can't diverge from it).
const BBCODE_SENTINEL_TAGS = {
  uOpen: '[U]',
  uClose: '[/U]',
  colorOpen: (hex) => `[COLOR=${hex}]`,
  colorClose: '[/COLOR]',
}
const BBCODE_SENTINEL_GUARDS = {
  // Markers inside [ICODE]/[CODE] stay literal, matching the Reader.
  code: /\[ICODE\][\s\S]*?\[\/ICODE\]|\[CODE(?:=[^\]]*)?\][\s\S]*?\[\/CODE\]/g,
  // Pairs never span block boundaries (Reader rule): a blank line between
  // blocks or any block-level BBCode tag in the between-span keeps both
  // markers literal. (Non-global on purpose — .test on /g/ is stateful.)
  block: /\n\n|\[\/?(?:QUOTE|LIST|TABLE|TR|TH|TD|CODE)(?:=[^\]]*)?\]/,
}
const bbSentinels = (s) => replaceInlineSentinels(s, BBCODE_SENTINEL_TAGS, BBCODE_SENTINEL_GUARDS)

// XenForo [SIZE] runs 1–7 (body text ≈ 3). [HEADING=n] exists on XF 2.2+ but
// bold+size renders everywhere and stays subdued in threadmarked chapters.
const HEADING_SIZE = { 1: 7, 2: 6, 3: 5, 4: 4, 5: 3, 6: 3 }
// [HR] is not core XenForo 2 BBCode; a bare --- line is the usual scene-break
// convention on these boards.
const HR = '---'

/** Convert a stored content line array to a BBCode string. */
export function linesToBBCode(lines, { illustrationUrls = {}, title = null } = {}) {
  const blocks = []
  if (title) blocks.push(`[B][SIZE=6]${title}[/SIZE][/B]`)
  for (const seg of splitSegments(lines || [])) {
    if (seg.type === 'img') {
      const url = illustrationUrls[seg.id]
      blocks.push(url
        ? `[IMG]${url}[/IMG]`
        : `[Illustration ${seg.id} — no public image URL; attach manually]`)
    } else if (seg.type === 'table') {
      blocks.push(renderRichTable(seg))
    } else if (seg.md.trim()) {
      blocks.push(...renderBlocks(parseMarkdownTokens(seg.md)))
    }
  }
  return bbSentinels(blocks.join('\n\n'))
}

/** Convert a TipTap editor doc (via its markdown lines) to BBCode. */
export function docToBBCode(doc, opts) {
  return linesToBBCode(docToLines(doc), opts)
}

// ---------------------------------------------------------------------------
// Block tokens
// ---------------------------------------------------------------------------

function findClose(tokens, openIdx, closeType) {
  const level = tokens[openIdx].level
  for (let j = openIdx + 1; j < tokens.length; j += 1) {
    if (tokens[j].type === closeType && tokens[j].level === level) return j
  }
  return tokens.length - 1
}

function inlineBetween(tokens, open, close) {
  for (let j = open + 1; j < close; j += 1) {
    if (tokens[j].type === 'inline') return tokens[j].children || []
  }
  return []
}

function renderBlocks(tokens, start = 0, end = tokens.length) {
  const blocks = []
  let i = start
  while (i < end) {
    const tok = tokens[i]
    switch (tok.type) {
      case 'paragraph_open': {
        const close = findClose(tokens, i, 'paragraph_close')
        const inner = renderInline(inlineBetween(tokens, i, close))
        if (inner) blocks.push(inner)
        i = close + 1
        break
      }
      case 'heading_open': {
        const close = findClose(tokens, i, 'heading_close')
        const level = Number(tok.tag.slice(1)) || 1
        const inner = renderInline(inlineBetween(tokens, i, close))
        blocks.push(`[B][SIZE=${HEADING_SIZE[level]}]${inner}[/SIZE][/B]`)
        i = close + 1
        break
      }
      case 'blockquote_open': {
        const close = findClose(tokens, i, 'blockquote_close')
        const inner = renderBlocks(tokens, i + 1, close).join('\n\n')
        blocks.push(`[QUOTE]\n${inner}\n[/QUOTE]`)
        i = close + 1
        break
      }
      case 'bullet_list_open':
      case 'ordered_list_open': {
        const ordered = tok.type === 'ordered_list_open'
        const close = findClose(tokens, i, ordered ? 'ordered_list_close' : 'bullet_list_close')
        blocks.push(renderList(tokens, i, close, ordered))
        i = close + 1
        break
      }
      case 'hr':
        blocks.push(HR)
        i += 1
        break
      case 'fence':
      case 'code_block': {
        const lang = (tok.info || '').trim()
        const content = (tok.content || '').replace(/\n$/, '')
        blocks.push(`[CODE${lang ? `=${lang}` : ''}]\n${content}\n[/CODE]`)
        i += 1
        break
      }
      case 'table_open': {
        const close = findClose(tokens, i, 'table_close')
        blocks.push(renderTable(tokens, i, close))
        i = close + 1
        break
      }
      default:
        i += 1 // structural wrappers (thead/tbody/…) and anything unknown
    }
  }
  return blocks
}

function renderList(tokens, open, close, ordered) {
  const items = []
  let i = open + 1
  while (i < close) {
    if (tokens[i].type === 'list_item_open') {
      const itemClose = findClose(tokens, i, 'list_item_close')
      items.push(`[*]${renderBlocks(tokens, i + 1, itemClose).join('\n')}`)
      i = itemClose + 1
    } else {
      i += 1
    }
  }
  // XenForo numbers [LIST=1] items itself; a non-1 `start` has no BBCode form.
  return `[LIST${ordered ? '=1' : ''}]\n${items.join('\n')}\n[/LIST]`
}

// Sentinel ⟦TABLE⟧ segment → XenForo table BBCode. Cells carry full block
// BBCode (lists, quotes, multi-line) — XenForo rows are explicitly terminated,
// which is the whole reason the sentinel format exists. The storage markers
// map 1:1 onto the BBCode tags.
function renderRichTable(seg) {
  const rows = (seg.rows || []).map((row) => {
    const cells = row.cells.map((cell) => {
      const tag = cell.header ? 'TH' : 'TD'
      const md = cell.lines.join('\n')
      const inner = md.trim() ? renderBlocks(parseMarkdownTokens(md)).join('\n\n') : ''
      return `[${tag}]${inner}[/${tag}]`
    })
    return `[TR]${cells.join('')}[/TR]`
  })
  return `[TABLE]\n${rows.join('\n')}\n[/TABLE]`
}

function renderTable(tokens, open, close) {
  const rows = []
  let i = open + 1
  while (i < close) {
    if (tokens[i].type === 'tr_open') {
      const trClose = findClose(tokens, i, 'tr_close')
      const cells = []
      let j = i + 1
      while (j < trClose) {
        const t = tokens[j].type
        if (t === 'th_open' || t === 'td_open') {
          const tag = t === 'th_open' ? 'TH' : 'TD'
          const cellClose = findClose(tokens, j, t === 'th_open' ? 'th_close' : 'td_close')
          cells.push(`[${tag}]${renderInline(inlineBetween(tokens, j, cellClose))}[/${tag}]`)
          j = cellClose + 1
        } else {
          j += 1
        }
      }
      rows.push(`[TR]${cells.join('')}[/TR]`)
      i = trClose + 1
    } else {
      i += 1
    }
  }
  return `[TABLE]\n${rows.join('\n')}\n[/TABLE]`
}

// ---------------------------------------------------------------------------
// Inline tokens
// ---------------------------------------------------------------------------

// A link whose href is just the linkified form of its own text: emit the bare
// URL and let XenForo's own auto-linker handle it.
function isAutoLink(href, text) {
  return href === text || href === `http://${text}` ||
    href === `https://${text}` || href === `mailto:${text}`
}

// Prose containing a literal BBCode tag XenForo would act on ([b], [quote=…],
// [url]…) gets wrapped in [PLAIN] so it renders as text. Restricted to tags
// XenForo actually parses — bracketed prose like "[1]" footnote markers,
// "[666]" danmaku counts, or "[Chapter cleared]" must pass through untouched.
const XF_TAG_RE = /\[\/?(?:B|I|U|S|URL|IMG|QUOTE|CODE|ICODE|LIST|TABLE|TR|TD|TH|COLOR|SIZE|FONT|SPOILER|ISPOILER|CENTER|LEFT|RIGHT|INDENT|HEADING|MEDIA|ATTACH|USER|EMAIL|PLAIN|\*)(?:=[^\]]*)?\]/i

function escapeText(text) {
  if (!XF_TAG_RE.test(text)) return text
  return `[PLAIN]${text}[/PLAIN]`
}

function renderInline(children) {
  let out = ''
  for (let k = 0; k < children.length; k += 1) {
    const tok = children[k]
    switch (tok.type) {
      case 'text':
      case 'text_special':
        out += escapeText(tok.content)
        break
      case 'softbreak':
      case 'hardbreak':
        out += '\n'
        break
      case 'code_inline':
        out += `[ICODE]${tok.content}[/ICODE]`
        break
      case 'strong_open': out += '[B]'; break
      case 'strong_close': out += '[/B]'; break
      case 'em_open': out += '[I]'; break
      case 'em_close': out += '[/I]'; break
      case 's_open': out += '[S]'; break
      case 's_close': out += '[/S]'; break
      case 'link_open': {
        const href = tok.attrGet('href') || ''
        let close = k + 1
        while (close < children.length && children[close].type !== 'link_close') close += 1
        const inner = children.slice(k + 1, close)
        const innerText = inner.length === 1 && inner[0].type === 'text' ? inner[0].content : null
        if (innerText !== null && (tok.markup === 'linkify' || isAutoLink(href, innerText))) {
          out += innerText
        } else {
          out += `[URL=${href}]${renderInline(inner)}[/URL]`
        }
        k = close
        break
      }
      default:
        out += tok.content || ''
    }
  }
  return out
}
