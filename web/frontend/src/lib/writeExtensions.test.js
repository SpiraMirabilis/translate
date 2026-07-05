/**
 * @vitest-environment jsdom
 *
 * Integration test: the real TipTap editor (schema built from
 * writeExtensions) must accept the bridge's documents unchanged, and its
 * getJSON() output must survive the markdown round-trip. This pins the
 * bridge's node/mark names and attrs to the actual schema — a rename or
 * schema drift in a TipTap upgrade fails here instead of blocking saves
 * in production.
 */
import { describe, it, expect } from 'vitest'
import { Editor } from '@tiptap/core'
import { buildWriteExtensions } from './writeExtensions'
import { linesToDoc, normalizeDoc, roundTrip } from './writeMarkdown'

const SAMPLE_LINES = [
  '## Chapter One — The Long Road',
  '',
  '“You’re late,” the ferryman said. **Very** late.',
  'Second line of the same paragraph.',
  '',
  '> A quoted line with *emphasis*.',
  '',
  '- first item',
  '- second item',
  '',
  '1. numbered',
  '2. items',
  '',
  '---',
  '',
  'A [link](https://example.com) and `code` and ~~strike~~.',
  '',
  'Some ⟦U⟧underlined⟦/U⟧ and ⟦COLOR:#e11d48⟧colored⟦/COLOR⟧ text.',
  '',
  '⟦U⟧**bold under**⟦/U⟧ and `⟦U⟧ literal in code ⟦/U⟧` stay distinct.',
  '',
  'She paid him[1] anyway.',
  '',
  '[1] - A footnote definition.',
  '',
  '⟦IMG:abc123⟧',
  '',
  '| Stat | Value |',
  '| :---: | --- |',
  '| STR | 18 |',
  '',
  '| System notice: you have leveled up. |',
  '| --- |',
  '',
  '⟦TABLE⟧',
  '⟦TR⟧',
  '⟦TH:center⟧', 'Stat', '⟦/TH⟧',
  '⟦/TR⟧',
  '⟦TR⟧',
  '⟦TD⟧', 'HP: 100', 'MP: 50', '', '- Fireball', '⟦/TD⟧',
  '⟦/TR⟧',
  '⟦/TABLE⟧',
  '',
  'Closing paragraph.',
]

function makeEditor(content) {
  return new Editor({
    element: document.createElement('div'),
    extensions: buildWriteExtensions(),
    content,
  })
}

describe('TipTap schema ⇄ writeMarkdown bridge', () => {
  it('accepts a bridge doc unchanged and getJSON round-trips to the same lines', () => {
    const { doc, unsupported } = linesToDoc(SAMPLE_LINES)
    expect(unsupported).toEqual([])

    const editor = makeEditor(doc)
    try {
      const json = editor.getJSON()
      // Schema must not have stripped or renamed anything the bridge emits.
      expect(JSON.stringify(normalizeDoc(json))).toBe(JSON.stringify(normalizeDoc(doc)))
      // And the editor's own JSON must serialize back to the exact lines.
      const rt = roundTrip(json)
      expect(rt.warnings).toEqual([])
      expect(rt.ok).toBe(true)
      expect(rt.lines).toEqual(SAMPLE_LINES)
    } finally {
      editor.destroy()
    }
  })

  it('insertTable command output round-trips', () => {
    const editor = makeEditor({ type: 'doc', content: [{ type: 'paragraph' }] })
    try {
      editor.chain().focus().insertTable({ rows: 2, cols: 2, withHeaderRow: true }).run()
      editor.commands.insertContent('Head')
      const rt = roundTrip(editor.getJSON())
      expect(rt.warnings).toEqual([])
      expect(rt.ok).toBe(true)
      expect(rt.lines).toContain('| --- | --- |')
    } finally {
      editor.destroy()
    }
  })

  it('Enter inside a cell paragraph inserts a hard break (XenForo behavior)', () => {
    const { doc } = linesToDoc(['| h |', '| --- |', '| body |'])
    const editor = makeEditor(doc)
    try {
      // Place the caret inside the body cell's paragraph, after "body".
      let pos = null
      editor.state.doc.descendants((node, p) => {
        if (node.isText && node.text === 'body') pos = p + node.nodeSize
        return pos == null
      })
      editor.commands.setTextSelection(pos)
      editor.view.someProp('handleKeyDown', (f) =>
        f(editor.view, new KeyboardEvent('keydown', { key: 'Enter' })))
      const rt = roundTrip(editor.getJSON())
      expect(rt.ok).toBe(true)
      // The cell became rich (trailing hard break canonicalizes away only if
      // empty — type after the break to keep it observable)
      editor.commands.insertContent('more')
      const rt2 = roundTrip(editor.getJSON())
      expect(rt2.ok).toBe(true)
      expect(rt2.lines[0]).toBe('⟦TABLE⟧')
      expect(rt2.lines).toContain('body')
      expect(rt2.lines).toContain('more')
    } finally {
      editor.destroy()
    }
  })

  it('Enter inside a list-in-cell splits the list item (list keymap wins)', () => {
    const { doc } = linesToDoc([
      '⟦TABLE⟧',
      '⟦TR⟧', '⟦TH⟧', 'h', '⟦/TH⟧', '⟦/TR⟧',
      '⟦TR⟧', '⟦TD⟧', '- item one', '⟦/TD⟧', '⟦/TR⟧',
      '⟦/TABLE⟧',
    ])
    const editor = makeEditor(doc)
    try {
      let pos = null
      editor.state.doc.descendants((node, p) => {
        if (node.isText && node.text === 'item one') pos = p + node.nodeSize
        return pos == null
      })
      editor.commands.setTextSelection(pos)
      editor.view.someProp('handleKeyDown', (f) =>
        f(editor.view, new KeyboardEvent('keydown', { key: 'Enter' })))
      editor.commands.insertContent('item two')
      const rt = roundTrip(editor.getJSON())
      expect(rt.ok).toBe(true)
      expect(rt.lines).toContain('- item one')
      expect(rt.lines).toContain('- item two')
    } finally {
      editor.destroy()
    }
  })

  it('Enter outside tables still splits the paragraph', () => {
    const { doc } = linesToDoc(['first words'])
    const editor = makeEditor(doc)
    try {
      editor.commands.setTextSelection(editor.state.doc.content.size - 1)
      editor.view.someProp('handleKeyDown', (f) =>
        f(editor.view, new KeyboardEvent('keydown', { key: 'Enter' })))
      editor.commands.insertContent('second words')
      const rt = roundTrip(editor.getJSON())
      expect(rt.ok).toBe(true)
      expect(rt.lines).toEqual(['first words', '', 'second words'])
    } finally {
      editor.destroy()
    }
  })

  it('editor-typed content (insert + marks) round-trips', () => {
    const editor = makeEditor({ type: 'doc', content: [{ type: 'paragraph' }] })
    try {
      editor.chain()
        .insertContent('First paragraph. ')
        .setMark('bold')
        .insertContent('bold words')
        .unsetMark('bold')
        .insertContent(' after.')
        .run()
      const rt = roundTrip(editor.getJSON())
      expect(rt.ok).toBe(true)
      expect(rt.lines).toEqual(['First paragraph. **bold words** after.'])
    } finally {
      editor.destroy()
    }
  })

  it('toggleUnderline and setColor commands produce sentinel lines', () => {
    const editor = makeEditor({ type: 'doc', content: [{ type: 'paragraph' }] })
    try {
      editor.chain()
        .insertContent('plain ')
        .setMark('underline')
        .insertContent('under')
        .unsetMark('underline')
        .insertContent(' and ')
        .setColor('#FF0080') // uppercase in, canonical lowercase out
        .insertContent('pink')
        .unsetColor()
        .insertContent(' done.')
        .run()
      const rt = roundTrip(editor.getJSON())
      expect(rt.warnings).toEqual([])
      expect(rt.ok).toBe(true)
      expect(rt.lines).toEqual(['plain ⟦U⟧under⟦/U⟧ and ⟦COLOR:#ff0080⟧pink⟦/COLOR⟧ done.'])
    } finally {
      editor.destroy()
    }
  })

  it('code spans keep coexisting marks (FlexibleCode, no excludes stripping)', () => {
    const { doc } = linesToDoc(['**`bold code`** and ⟦U⟧`under code`⟦/U⟧'])
    const editor = makeEditor(doc)
    try {
      // Schema must not strip bold/underline off code text nodes.
      expect(JSON.stringify(normalizeDoc(editor.getJSON()))).toBe(JSON.stringify(normalizeDoc(doc)))
      const rt = roundTrip(editor.getJSON())
      expect(rt.ok).toBe(true)
      expect(rt.lines).toEqual(['**`bold code`** and ⟦U⟧`under code`⟦/U⟧'])
    } finally {
      editor.destroy()
    }
  })
})
