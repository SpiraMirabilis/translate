/**
 * @vitest-environment jsdom
 *
 * The suggestions-pane additions to the grammar plugin: kind-level view
 * filtering (setGrammarHiddenKinds), rule-level removal (ignoreGrammarRule),
 * and direct activation (activateGrammarIssueById).
 */
import { describe, it, expect } from 'vitest'
import { Editor } from '@tiptap/core'
import { buildWriteExtensions } from './writeExtensions'
import { grammarPluginKey } from './grammarExtension'
import { linesToDoc, roundTrip } from './writeMarkdown'
import { extractDocBlocks, locateFind } from './grammarOffsets'

// "The teh quick colour fox." — teh: typo, colour: style (Oxford-ish nag).
const TEXT = 'The teh quick colour fox.'

const issue = (id, kind, ruleId, originalText) => ({
  id, source: 'lt', kind, ruleId, originalText,
  message: '', shortMessage: '', replacements: ['x'],
})

function makeEditor() {
  const editor = new Editor({
    element: document.createElement('div'),
    extensions: buildWriteExtensions(),
    content: { type: 'doc', content: [{ type: 'paragraph', content: [{ type: 'text', text: TEXT }] }] },
  })
  // Paragraph content starts at pos 1, so text offset k = pos k+1.
  const at = (word) => {
    const off = TEXT.indexOf(word)
    return { from: off + 1, to: off + 1 + word.length }
  }
  editor.commands.setGrammarResults('lt', [
    { ...at('teh'), issue: issue('i-typo', 'typo', 'MORFOLOGIK_RULE_EN_US', 'teh') },
    { ...at('colour'), issue: issue('i-style', 'style', 'OXFORD_SPELLING_Z_NOT_S', 'colour') },
  ])
  return { editor, at }
}

const pluginState = (editor) => grammarPluginKey.getState(editor.state)
const decoFor = (editor, id) =>
  pluginState(editor).decos.find(undefined, undefined, (s) => s.issue.id === id)[0] || null

describe('grammarExtension: hidden kinds / ignore rule / activate by id', () => {
  it('setGrammarHiddenKinds hides squiggles without dropping decorations', () => {
    const { editor } = makeEditor()
    try {
      editor.commands.setGrammarHiddenKinds(['style'])
      expect(decoFor(editor, 'i-style').type.attrs.class).toContain('gram-hidden')
      expect(decoFor(editor, 'i-typo').type.attrs.class).not.toContain('gram-hidden')
      // Counts hold: both decorations still present.
      expect(pluginState(editor).decos.find()).toHaveLength(2)

      // Un-hiding restores the squiggle.
      editor.commands.setGrammarHiddenKinds([])
      expect(decoFor(editor, 'i-style').type.attrs.class).not.toContain('gram-hidden')
    } finally {
      editor.destroy()
    }
  })

  it('results set while a kind is hidden come in hidden', () => {
    const { editor, at } = makeEditor()
    try {
      editor.commands.setGrammarHiddenKinds(['style'])
      editor.commands.setGrammarResults('lt', [
        { ...at('colour'), issue: issue('i-style2', 'style', 'SOME_STYLE_RULE', 'colour') },
      ])
      expect(decoFor(editor, 'i-style2').type.attrs.class).toContain('gram-hidden')
    } finally {
      editor.destroy()
    }
  })

  it('hiding the active issue clears the activation', () => {
    const { editor } = makeEditor()
    try {
      editor.commands.activateGrammarIssue('i-style')
      expect(pluginState(editor).activeId).toBe('i-style')
      editor.commands.setGrammarHiddenKinds(['style'])
      expect(pluginState(editor).activeId).toBe(null)
      expect(decoFor(editor, 'i-style').type.attrs.class).not.toContain('gram-active')
    } finally {
      editor.destroy()
    }
  })

  it('activateNextGrammarIssue skips hidden kinds', () => {
    const { editor } = makeEditor()
    try {
      editor.commands.setGrammarHiddenKinds(['style'])
      editor.commands.setTextSelection(1)
      editor.commands.activateNextGrammarIssue(1)
      expect(pluginState(editor).activeId).toBe('i-typo')
      // Wraps back to the typo instead of landing on the hidden style issue.
      editor.commands.activateNextGrammarIssue(1)
      expect(pluginState(editor).activeId).toBe('i-typo')
    } finally {
      editor.destroy()
    }
  })

  it('ignoreGrammarRule removes every decoration for the rule', () => {
    const { editor, at } = makeEditor()
    try {
      editor.commands.setGrammarResults('lt', [
        { ...at('teh'), issue: issue('a', 'style', 'OXFORD_SPELLING_Z_NOT_S', 'teh') },
        { ...at('colour'), issue: issue('b', 'style', 'OXFORD_SPELLING_Z_NOT_S', 'colour') },
        { ...at('quick'), issue: issue('c', 'grammar', 'OTHER_RULE', 'quick') },
      ])
      editor.commands.activateGrammarIssue('a')
      editor.commands.ignoreGrammarRule('OXFORD_SPELLING_Z_NOT_S')
      const remaining = pluginState(editor).decos.find().map((d) => d.spec.issue.id)
      expect(remaining).toEqual(['c'])
      expect(pluginState(editor).activeId).toBe(null)
    } finally {
      editor.destroy()
    }
  })

  it('applyGrammarSuggestion preserves marks the plain-text check cannot see', () => {
    // The checker runs on plain text, so flagged ranges know nothing about
    // formatting runs. The apply diff must leave untouched characters (and
    // their marks) alone.
    const cases = [
      { // typo inside an italic phrase stays italic
        lines: ['The best of times, *the wrost of times*.'],
        find: 'wrost', replace: 'worst',
        expected: ['The best of times, *the worst of times*.'],
      },
      { // polish-style find spanning a formatting boundary: the inserted
        // comma lands outside the italics, which survive intact
        lines: ['plain *italic tail* end.'],
        find: 'plain italic', replace: 'plain, italic',
        expected: ['plain, *italic tail* end.'],
      },
      { // whole-phrase replacement where the changed core is inside the run
        lines: ['He said **the wrost thing** possible.'],
        find: 'the wrost thing', replace: 'the worst thing',
        expected: ['He said **the worst thing** possible.'],
      },
      { // deletion (empty replacement) still works
        lines: ['one twwo three'],
        find: 'twwo ', replace: '',
        expected: ['one three'],
      },
      { // sub-word color on a character the fix doesn't touch survives:
        // wrost→worst only rewrites "ro", the colored "w" is never touched
        lines: ['The ⟦COLOR:#ff0000⟧w⟦/COLOR⟧rost of times.'],
        find: 'wrost', replace: 'worst',
        expected: ['The ⟦COLOR:#ff0000⟧w⟦/COLOR⟧orst of times.'],
      },
      { // a colored run elsewhere inside a long polish find is untouched:
        // the diff narrows "wrost of times"→"worst of times" down to "ro"
        lines: ['The wrost ⟦COLOR:#00ff00⟧of⟦/COLOR⟧ times.'],
        find: 'wrost of times', replace: 'worst of times',
        expected: ['The worst ⟦COLOR:#00ff00⟧of⟦/COLOR⟧ times.'],
      },
      { // known ambiguity: a colored char INSIDE the changed core ("ro"→"or"
        // where the "o" was red) has no principled destination — the new core
        // takes the first changed character's marks (plain here), color drops
        lines: ['The wr⟦COLOR:#ff0000⟧o⟦/COLOR⟧st of times.'],
        find: 'wrost', replace: 'worst',
        expected: ['The worst of times.'],
      },
    ]
    for (const c of cases) {
      const { doc, unsupported } = linesToDoc(c.lines)
      expect(unsupported).toEqual([])
      const editor = new Editor({
        element: document.createElement('div'),
        extensions: buildWriteExtensions(),
        content: doc,
      })
      try {
        const { blocks, meta } = extractDocBlocks(editor.state.doc)
        const range = locateFind(blocks, meta, c.find)
        expect(range).not.toBe(null)
        editor.commands.setGrammarResults('lt', [
          { ...range, issue: issue('fix', 'grammar', 'RULE', c.find) },
        ])
        expect(editor.commands.applyGrammarSuggestion('fix', c.replace)).toBe(true)
        const rt = roundTrip(editor.getJSON())
        expect(rt.ok).toBe(true)
        expect(rt.lines).toEqual(c.expected)
      } finally {
        editor.destroy()
      }
    }
  })

  it('activateGrammarIssueById activates and moves the selection', () => {
    const { editor, at } = makeEditor()
    try {
      expect(editor.commands.activateGrammarIssueById('i-style')).toBe(true)
      expect(pluginState(editor).activeId).toBe('i-style')
      expect(editor.state.selection.from).toBe(at('colour').from)
      expect(decoFor(editor, 'i-style').type.attrs.class).toContain('gram-active')
      // Unknown id is a no-op.
      expect(editor.commands.activateGrammarIssueById('nope')).toBe(false)
    } finally {
      editor.destroy()
    }
  })
})
