/**
 * @vitest-environment jsdom
 *
 * grammarOffsets — plain-text extraction + PM position mapping for the
 * grammar checker. The invariant under test: for every extracted block,
 * character k sits at PM position pmStart + k, so backend matches
 * (block, offset, length) land exactly on the flagged text.
 */
import { describe, it, expect } from 'vitest'
import { getSchema } from '@tiptap/core'
import { Node } from '@tiptap/pm/model'
import { buildWriteExtensions } from './writeExtensions'
import { linesToDoc } from './writeMarkdown'
import { extractDocBlocks, blockToPm, locateFind } from './grammarOffsets'

const schema = getSchema(buildWriteExtensions())

const fromLines = (lines) => {
  const { doc, unsupported } = linesToDoc(lines)
  expect(unsupported).toEqual([])
  return Node.fromJSON(schema, doc)
}

/** Every (block, offset) must round-trip: doc text at mapped range == slice. */
function expectAllOffsetsMap(doc) {
  const { blocks, meta } = extractDocBlocks(doc)
  blocks.forEach((text, bi) => {
    for (let off = 0; off < text.length; off += 1) {
      for (const len of [1, Math.min(4, text.length - off)]) {
        if (len < 1) continue
        const r = blockToPm(meta, bi, off, len)
        expect(r).not.toBeNull()
        expect(doc.textBetween(r.from, r.to, '\n', '\n')).toBe(text.slice(off, off + len))
      }
    }
  })
  return { blocks, meta }
}

describe('extractDocBlocks + blockToPm', () => {
  it('single paragraph maps every offset', () => {
    const doc = fromLines(['The rain had stopped by dawn.'])
    const { blocks } = expectAllOffsetsMap(doc)
    expect(blocks).toEqual(['The rain had stopped by dawn.'])
  })

  it('multiple paragraphs and headings', () => {
    const doc = fromLines(['# The Ferry', '', 'First paragraph.', '', 'Second one.'])
    const { blocks } = expectAllOffsetsMap(doc)
    expect(blocks).toEqual(['The Ferry', 'First paragraph.', 'Second one.'])
  })

  it('hardBreak contributes exactly one character', () => {
    const doc = fromLines(['Line one.', 'Line two.'])
    const { blocks } = expectAllOffsetsMap(doc)
    expect(blocks).toEqual(['Line one.\nLine two.'])
  })

  it('blockquotes and nested lists capture innermost textblocks', () => {
    const doc = fromLines(['> A quote.', '', '- outer item', '  - inner item'])
    const { blocks } = expectAllOffsetsMap(doc)
    expect(blocks).toContain('A quote.')
    expect(blocks).toContain('outer item')
    expect(blocks).toContain('inner item')
  })

  it('code blocks, tables, and illustrations are excluded; neighbors still map', () => {
    const doc = fromLines([
      'Before.',
      '',
      '```',
      'const x = 1',
      '```',
      '',
      '| A | B |',
      '| --- | --- |',
      '| 1 | 2 |',
      '',
      '⟦IMG:abc123⟧',
      '',
      'After.',
    ])
    const { blocks } = expectAllOffsetsMap(doc)
    expect(blocks).toEqual(['Before.', 'After.'])
  })

  it('marks do not affect offsets', () => {
    const doc = fromLines(['Some **bold** and *italic* prose here.'])
    const { blocks, meta } = expectAllOffsetsMap(doc)
    expect(blocks[0]).toBe('Some bold and italic prose here.')
    const r = blockToPm(meta, 0, 5, 4)
    expect(doc.textBetween(r.from, r.to)).toBe('bold')
  })

  it('out-of-range offsets return null', () => {
    const doc = fromLines(['short'])
    const { meta } = extractDocBlocks(doc)
    expect(blockToPm(meta, 0, 3, 10)).toBeNull()
    expect(blockToPm(meta, 5, 0, 1)).toBeNull()
    expect(blockToPm(meta, 0, -1, 1)).toBeNull()
  })

  it('kitchen-sink invariant: block text length equals content size', () => {
    const doc = fromLines([
      '## Scene',
      '',
      'Prose with **marks** and a',
      'hard-break line.',
      '',
      '> quoted text',
      '',
      '1. first',
      '2. second',
    ])
    // extractDocBlocks throws if the invariant is violated.
    expect(() => extractDocBlocks(doc)).not.toThrow()
  })
})

describe('locateFind', () => {
  it('finds the first occurrence and maps it', () => {
    const doc = fromLines(['He had went home.', '', 'Then he had went again.'])
    const { blocks, meta } = extractDocBlocks(doc)
    const r = locateFind(blocks, meta, 'had went')
    expect(doc.textBetween(r.from, r.to)).toBe('had went')
    // First occurrence = block 0
    expect(r.from).toBe(meta[0].pmStart + 3)
  })

  it('returns null when absent or spanning blocks', () => {
    const doc = fromLines(['Alpha end.', '', 'Beta start.'])
    const { blocks, meta } = extractDocBlocks(doc)
    expect(locateFind(blocks, meta, 'not present')).toBeNull()
    expect(locateFind(blocks, meta, 'end.\n\nBeta')).toBeNull()
    expect(locateFind(blocks, meta, '')).toBeNull()
  })

  it('finds text after a hard break within one block', () => {
    const doc = fromLines(['First line.', 'Second line here.'])
    const { blocks, meta } = extractDocBlocks(doc)
    const r = locateFind(blocks, meta, 'Second line')
    expect(r).not.toBeNull()
    expect(doc.textBetween(r.from, r.to, '\n', '\n')).toBe('Second line')
  })
})
