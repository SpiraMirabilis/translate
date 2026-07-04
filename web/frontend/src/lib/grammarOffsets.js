// Plain-text extraction + offset mapping between the write editor's
// ProseMirror doc and the grammar backend (/api/grammar/check).
//
// The backend receives an array of plain-text block strings (never stored
// markdown lines — those contain **/#/> syntax LanguageTool would flag) and
// returns matches as { block, offset, length }. This module owns both
// directions:
//   extractDocBlocks(doc)  → { blocks: [string], meta: [{ pmStart, length }] }
//   blockToPm(meta, block, offset, length) → { from, to } | null
//   locateFind(blocks, meta, find) → { from, to } | null   (polish pass)
//
// Invariant: within a textblock, character k of its extracted text sits at
// PM position meta.pmStart + k — every inline leaf is either a text node
// (1 char = 1 position) or a hardBreak (emits exactly '\n', nodeSize 1).
// The schema (writeExtensions.js) has no other inline leaves; the dev
// assertion below fails tests loudly if that ever changes, instead of
// letting squiggles drift.

// Block node types excluded from checking: code isn't prose; table cells are
// fragments that bait false sentence-level positives (v2 could emit each cell
// as its own block); illustrations are atoms with no text.
const SKIP_TYPES = new Set(['codeBlock', 'table', 'illustration'])

/**
 * Walk the doc's textblocks and extract plain text per block.
 * Returns { blocks, meta } — parallel arrays (strings / position info).
 */
export function extractDocBlocks(doc) {
  const blocks = []
  const meta = []
  doc.descendants((node, pos) => {
    if (SKIP_TYPES.has(node.type.name)) return false
    if (!node.isTextblock) return true // recurse into blockquote/list wrappers
    const text = node.textBetween(0, node.content.size, undefined, (leaf) =>
      leaf.type.name === 'hardBreak' ? '\n' : '')
    if (text.length !== node.content.size) {
      // A new inline leaf type broke the 1-char-per-position invariant.
      throw new Error(
        `grammarOffsets: block text length ${text.length} != content size ${node.content.size} (${node.type.name})`)
    }
    blocks.push(text)
    meta.push({ pmStart: pos + 1, length: text.length })
    return false // textblocks contain only inline content
  })
  return { blocks, meta }
}

/** Map a backend match (block index + in-block offset) to PM positions. */
export function blockToPm(meta, block, offset, length) {
  const m = meta[block]
  if (!m) return null
  if (offset < 0 || offset + length > m.length) return null
  return { from: m.pmStart + offset, to: m.pmStart + offset + length }
}

/**
 * Locate a polish `find` string in the extracted blocks (first occurrence).
 * Search per block — a find that spans two blocks has no contiguous PM range
 * (block boundary) and returns null (callers put it on the fallback list).
 */
export function locateFind(blocks, meta, find) {
  if (!find) return null
  for (let i = 0; i < blocks.length; i += 1) {
    const idx = blocks[i].indexOf(find)
    if (idx !== -1) return blockToPm(meta, i, idx, find.length)
  }
  return null
}
