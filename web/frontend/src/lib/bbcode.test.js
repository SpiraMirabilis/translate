/**
 * @vitest-environment jsdom
 *
 * bbcode — Markdown lines → XenForo BBCode (SB/SV/QQ). Fixtures cover the
 * full node set the write editor can produce, plus the doc-level entry point.
 */
import { describe, it, expect } from 'vitest'
import { linesToBBCode, docToBBCode } from './bbcode'
import { linesToDoc } from './writeMarkdown'

describe('linesToBBCode', () => {
  it('plain paragraphs join with blank lines', () => {
    expect(linesToBBCode(['First paragraph.', '', 'Second paragraph.']))
      .toBe('First paragraph.\n\nSecond paragraph.')
  })

  it('hard-break lines stay one block with single newlines', () => {
    expect(linesToBBCode(['Line one.', 'Line two.', '', 'Next.']))
      .toBe('Line one.\nLine two.\n\nNext.')
  })

  it('inline marks, including nesting', () => {
    expect(linesToBBCode(['Some **bold** and *italic* and ~~struck~~ and `code`.']))
      .toBe('Some [B]bold[/B] and [I]italic[/I] and [S]struck[/S] and [ICODE]code[/ICODE].')
    expect(linesToBBCode(['**bold with *italic* inside**']))
      .toBe('[B]bold with [I]italic[/I] inside[/B]')
  })

  it('headings map to bold + size by level', () => {
    expect(linesToBBCode(['# Chapter One'])).toBe('[B][SIZE=7]Chapter One[/SIZE][/B]')
    expect(linesToBBCode(['## Scene'])).toBe('[B][SIZE=6]Scene[/SIZE][/B]')
    expect(linesToBBCode(['### Sub'])).toBe('[B][SIZE=5]Sub[/SIZE][/B]')
  })

  it('blockquote, including multi-paragraph', () => {
    expect(linesToBBCode(['> A quote.'])).toBe('[QUOTE]\nA quote.\n[/QUOTE]')
    expect(linesToBBCode(['> First.', '>', '> Second.']))
      .toBe('[QUOTE]\nFirst.\n\nSecond.\n[/QUOTE]')
  })

  it('bullet and ordered lists, including nesting', () => {
    expect(linesToBBCode(['- one', '- two']))
      .toBe('[LIST]\n[*]one\n[*]two\n[/LIST]')
    expect(linesToBBCode(['1. first', '2. second']))
      .toBe('[LIST=1]\n[*]first\n[*]second\n[/LIST]')
    expect(linesToBBCode(['- outer', '  - inner']))
      .toBe('[LIST]\n[*]outer\n[LIST]\n[*]inner\n[/LIST]\n[/LIST]')
  })

  it('horizontal rule becomes a scene-break line', () => {
    expect(linesToBBCode(['before', '', '---', '', 'after']))
      .toBe('before\n\n---\n\nafter')
  })

  it('code blocks with and without language', () => {
    expect(linesToBBCode(['```js', 'const x = 1', '```']))
      .toBe('[CODE=js]\nconst x = 1\n[/CODE]')
    expect(linesToBBCode(['```', 'plain', '```']))
      .toBe('[CODE]\nplain\n[/CODE]')
  })

  it('tables map to XenForo table tags with TH header row', () => {
    expect(linesToBBCode(['| Name | HP |', '| --- | --- |', '| Slime | 10 |']))
      .toBe('[TABLE]\n[TR][TH]Name[/TH][TH]HP[/TH][/TR]\n[TR][TD]Slime[/TD][TD]10[/TD][/TR]\n[/TABLE]')
  })

  it('sentinel rich tables carry block content in cells', () => {
    const lines = [
      '⟦TABLE⟧',
      '⟦TR⟧', '⟦TH:center⟧', 'Stat', '⟦/TH⟧', '⟦/TR⟧',
      '⟦TR⟧', '⟦TD⟧', 'HP: 100', 'MP: 50', '', '- Fireball', '- Ice Lance', '⟦/TD⟧', '⟦/TR⟧',
      '⟦TR⟧', '⟦TD⟧', '⟦/TD⟧', '⟦/TR⟧',
      '⟦/TABLE⟧',
    ]
    expect(linesToBBCode(lines)).toBe([
      '[TABLE]',
      '[TR][TH]Stat[/TH][/TR]',
      '[TR][TD]HP: 100\nMP: 50\n\n[LIST]\n[*]Fireball\n[*]Ice Lance\n[/LIST][/TD][/TR]',
      '[TR][TD][/TD][/TR]',
      '[/TABLE]',
    ].join('\n'))
  })

  it('sentinel table parity between stored lines and editor doc', () => {
    const lines = [
      '⟦TABLE⟧',
      '⟦TR⟧', '⟦TH⟧', 'h', '⟦/TH⟧', '⟦/TR⟧',
      '⟦TR⟧', '⟦TD⟧', 'a', 'b', '⟦/TD⟧', '⟦/TR⟧',
      '⟦/TABLE⟧',
    ]
    const { doc, unsupported } = linesToDoc(lines)
    expect(unsupported).toEqual([])
    expect(docToBBCode(doc)).toBe(linesToBBCode(lines))
    expect(docToBBCode(doc)).toContain('[TD]a\nb[/TD]')
  })

  it('underline and color sentinels map to [U] and [COLOR=…]', () => {
    expect(linesToBBCode(['Some ⟦U⟧underlined⟦/U⟧ text.']))
      .toBe('Some [U]underlined[/U] text.')
    expect(linesToBBCode(['A ⟦COLOR:#ff0080⟧pink⟦/COLOR⟧ word.']))
      .toBe('A [COLOR=#ff0080]pink[/COLOR] word.')
    expect(linesToBBCode(['⟦U⟧**bold under**⟦/U⟧']))
      .toBe('[U][B]bold under[/B][/U]')
  })

  it('sentinels inside inline code stay literal; pairs may span code', () => {
    expect(linesToBBCode(['`⟦U⟧ raw ⟦/U⟧`']))
      .toBe('[ICODE]⟦U⟧ raw ⟦/U⟧[/ICODE]')
    expect(linesToBBCode(['⟦U⟧a `c` b⟦/U⟧']))
      .toBe('[U]a [ICODE]c[/ICODE] b[/U]')
  })

  it('unmatched sentinels stay literal in bbcode output', () => {
    expect(linesToBBCode(['stray ⟦U⟧ marker'])).toBe('stray ⟦U⟧ marker')
  })

  it('explicit links become [URL=…], bare autolinks stay bare', () => {
    expect(linesToBBCode(['See [the wiki](https://example.com/wiki) for more.']))
      .toBe('See [URL=https://example.com/wiki]the wiki[/URL] for more.')
    expect(linesToBBCode(['Read https://example.com today.']))
      .toBe('Read https://example.com today.')
  })

  it('illustrations emit [IMG] when a URL is known, placeholder otherwise', () => {
    const lines = ['before', '', '⟦IMG:abc123⟧', '', 'after']
    expect(linesToBBCode(lines, { illustrationUrls: { abc123: 'https://cdn.example.com/x.png' } }))
      .toBe('before\n\n[IMG]https://cdn.example.com/x.png[/IMG]\n\nafter')
    expect(linesToBBCode(lines))
      .toBe('before\n\n[Illustration abc123 — no public image URL; attach manually]\n\nafter')
  })

  it('optional title renders as a bold heading block', () => {
    expect(linesToBBCode(['Body.'], { title: 'Chapter 5' }))
      .toBe('[B][SIZE=6]Chapter 5[/SIZE][/B]\n\nBody.')
  })
})

describe('docToBBCode', () => {
  it('editor doc parity with stored-lines conversion', () => {
    const lines = [
      '## The Ferry',
      '',
      'The rain had **stopped** by the time Maren reached the crossing.',
      'She waited anyway.',
      '',
      '> "You’re late," the ferryman said.',
      '',
      '- coin',
      '- lantern',
      '',
      '---',
      '',
      'The far shore was *quiet*.',
    ]
    const { doc, unsupported } = linesToDoc(lines)
    expect(unsupported).toEqual([])
    expect(docToBBCode(doc)).toBe(linesToBBCode(lines))
    expect(docToBBCode(doc)).toContain('[B][SIZE=6]The Ferry[/SIZE][/B]')
    expect(docToBBCode(doc)).toContain('[QUOTE]')
    expect(docToBBCode(doc)).toContain('[LIST]')
  })
})
