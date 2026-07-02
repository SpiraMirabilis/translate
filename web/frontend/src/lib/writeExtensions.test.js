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
})
