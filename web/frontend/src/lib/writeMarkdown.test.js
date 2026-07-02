/**
 * @vitest-environment jsdom
 *
 * writeMarkdown — the WYSIWYG bridge. The invariant under test everywhere:
 * stored lines survive linesToDoc → docToLines byte-identical (for content in
 * the canonical form our serializer emits), and arbitrary editor docs pass
 * roundTrip() after normalization. A regression here corrupts chapters, so
 * these tests are deliberately exhaustive.
 */
import { describe, it, expect } from 'vitest'
import { linesToDoc, docToLines, normalizeDoc, roundTrip } from './writeMarkdown'

/** lines → doc → lines must be identity. */
function expectIdentity(lines) {
  const { doc, unsupported } = linesToDoc(lines)
  expect(unsupported).toEqual([])
  const warnings = []
  expect(docToLines(doc, warnings)).toEqual(lines)
  expect(warnings).toEqual([])
}

/** editor-born doc must round-trip cleanly. */
function expectRoundTrip(doc) {
  const result = roundTrip(doc)
  expect(result.warnings).toEqual([])
  expect(result.unsupported).toEqual([])
  expect(result.ok).toBe(true)
  return result.lines
}

const p = (...content) => ({ type: 'paragraph', content })
const text = (t, ...marks) => (marks.length ? { type: 'text', text: t, marks } : { type: 'text', text: t })
const br = { type: 'hardBreak' }

describe('identity round-trips (stored → doc → stored)', () => {
  it('plain paragraphs separated by blank lines (dominant real shape)', () => {
    expectIdentity([
      'The rain had stopped by the time Maren reached the crossing.',
      '',
      '"You’re late," the ferryman said.',
      '',
      'She dropped the coin into his palm without answering.',
    ])
  })

  it('consecutive non-empty lines stay one paragraph with hard breaks', () => {
    expectIdentity([
      'First line of the paragraph.',
      'Second line, same paragraph.',
      'Third line.',
      '',
      'A new paragraph.',
    ])
  })

  it('inline marks: bold, italic, strike, code, nesting', () => {
    expectIdentity([
      'Some **bold** and *italic* and ~~struck~~ and `code` text.',
      '',
      'Nested **bold with *italic* inside** it.',
      '',
      '**`bold code`**',
    ])
  })

  it('headings at all levels', () => {
    expectIdentity([
      '# One',
      '',
      '## Two',
      '',
      '### Three with **bold**',
      '',
      '###### Six',
    ])
  })

  it('blockquotes, including multi-paragraph', () => {
    expectIdentity([
      '> A single-line quote.',
      '',
      '> First quoted paragraph.',
      '>',
      '> Second quoted paragraph.',
    ])
  })

  it('horizontal rules (scene breaks)', () => {
    expectIdentity([
      'Before the break.',
      '',
      '---',
      '',
      'After the break.',
    ])
  })

  it('bullet and ordered lists, including start offset and nesting', () => {
    expectIdentity([
      '- first item',
      '- second item',
      '',
      '1. one',
      '2. two',
      '',
      'An interlude paragraph (a blank line alone does not split lists).',
      '',
      '3. starts at three',
      '4. four',
    ])
    expectIdentity([
      '- outer',
      '  - inner one',
      '  - inner two',
      '- outer two',
    ])
  })

  it('fenced code blocks', () => {
    expectIdentity([
      '```',
      'plain code',
      'second line',
      '```',
    ])
    expectIdentity([
      '```python',
      'def f():',
      '    return 1',
      '```',
    ])
  })

  it('explicit links', () => {
    expectIdentity([
      'See [the docs](https://example.com/page) for details.',
    ])
  })

  it('bare URLs stay bare (linkify re-links at render)', () => {
    expectIdentity([
      'Visit https://example.com/path today.',
      '',
      'Or www.example.org works too.',
    ])
  })

  it('footnote refs and definition lines are byte-identical', () => {
    expectIdentity([
      'The hexagram[1] glowed faintly, like a QQ status light[2].',
      '',
      '[1] - A divination symbol from the I Ching.',
      '',
      '[2] - Chinese instant-messaging service.',
    ])
  })

  it('⟦IMG:id⟧ markers at head, middle, and tail', () => {
    expectIdentity([
      '⟦IMG:a1b2c3⟧',
      '',
      'Text between illustrations.',
      '',
      '⟦IMG:d4e5f6⟧',
      '',
      'Closing text.',
      '',
      '⟦IMG:0099aa⟧',
    ])
  })

  it('typographic Unicode passes through untouched', () => {
    expectIdentity([
      '“Smart quotes,” she said — with an em-dash… and ‘single’ ones.',
      '',
      '中文字符 mixed with English.',
    ])
  })

  it('escaped construct-lookalikes stay escaped', () => {
    expectIdentity([
      '\\# Not a heading.',
      '',
      '\\> Not a quote.',
      '',
      '\\- Not a bullet.',
      '',
      '1\\. Not a list.',
      '',
      'Asterisks \\*not emphasis\\* here.',
    ])
  })
})

describe('escaping (doc → lines guards)', () => {
  it('escapes block-construct lookalikes typed as prose', () => {
    const cases = [
      '# looks like a heading',
      '> looks like a quote',
      '- looks like a bullet',
      '+ plus bullet',
      '1. looks like a list',
      '2) paren list',
      '---',
      '===',
    ]
    for (const line of cases) {
      const lines = expectRoundTrip({ type: 'doc', content: [p(text(line))] })
      expect(lines).toHaveLength(1)
      expect(lines[0]).not.toBe(line) // something was escaped
    }
  })

  it('escapes inline specials in prose', () => {
    const lines = expectRoundTrip({
      type: 'doc',
      content: [p(text('stars *here*, under_scores, `ticks`, ~~tildes~~, [brackets](x)'))],
    })
    expect(lines[0]).toContain('\\*here\\*')
    expect(lines[0]).toContain('\\`ticks\\`')
    expect(lines[0]).toContain('\\~~tildes\\~~') // first tilde of each pair escaped
    expect(lines[0]).toContain('\\[brackets](x)')
  })

  it('does not escape footnote-style [n] refs', () => {
    const lines = expectRoundTrip({ type: 'doc', content: [p(text('a ref[3] here'))] })
    expect(lines[0]).toBe('a ref[3] here')
  })

  it('setext lookalike under a paragraph line is guarded', () => {
    expectRoundTrip({ type: 'doc', content: [p(text('Title'), br, text('---'))] })
    expectRoundTrip({ type: 'doc', content: [p(text('Title'), br, text('==='))] })
  })

  it('leaves URLs unescaped even with markdown specials inside', () => {
    const lines = expectRoundTrip({
      type: 'doc',
      content: [p(text('see https://en.example.org/wiki/Foo_(bar)_baz now'))],
    })
    expect(lines[0]).toContain('https://en.example.org/wiki/Foo_(bar)_baz')
  })
})

describe('editor-born docs (doc → lines → doc)', () => {
  it('typical prose with smart quotes and marks', () => {
    const lines = expectRoundTrip({
      type: 'doc',
      content: [
        { type: 'heading', attrs: { level: 2 }, content: [text('Chapter 12 — The Long Road')] },
        p(text('“You’re late,” he said. '), text('Very', { type: 'bold' }), text(' late.')),
        p(text('A second paragraph.')),
      ],
    })
    expect(lines).toEqual([
      '## Chapter 12 — The Long Road',
      '',
      '“You’re late,” he said. **Very** late.',
      '',
      'A second paragraph.',
    ])
  })

  it('empty paragraphs are dropped', () => {
    const lines = expectRoundTrip({
      type: 'doc',
      content: [p(text('a')), { type: 'paragraph' }, p(text('b'))],
    })
    expect(lines).toEqual(['a', '', 'b'])
  })

  it('trailing and leading hard breaks are dropped', () => {
    const lines = expectRoundTrip({
      type: 'doc',
      content: [p(br, text('a'), br)],
    })
    expect(lines).toEqual(['a'])
  })

  it('double hard breaks split the paragraph', () => {
    const lines = expectRoundTrip({
      type: 'doc',
      content: [p(text('a'), br, br, text('b'))],
    })
    expect(lines).toEqual(['a', '', 'b'])
  })

  it('edge whitespace inside emphasis is expelled', () => {
    const lines = expectRoundTrip({
      type: 'doc',
      content: [p(text('a'), text(' bold ', { type: 'bold' }), text('b'))],
    })
    expect(lines).toEqual(['a **bold** b'])
  })

  it('bold spanning a hard break closes and reopens per line', () => {
    const lines = expectRoundTrip({
      type: 'doc',
      content: [p(text('one', { type: 'bold' }), { type: 'hardBreak', marks: [{ type: 'bold' }] }, text('two', { type: 'bold' }))],
    })
    expect(lines).toEqual(['**one**', '**two**'])
  })

  it('adjacent same-marked text nodes merge', () => {
    const lines = expectRoundTrip({
      type: 'doc',
      content: [p(text('one ', { type: 'bold' }), text('two', { type: 'bold' }))],
    })
    expect(lines).toEqual(['**one two**'])
  })

  it('link marks with TipTap default attrs serialize to href only', () => {
    const lines = expectRoundTrip({
      type: 'doc',
      content: [p(text('click', {
        type: 'link',
        attrs: { href: 'https://x.test/a', target: '_blank', rel: 'noopener', class: null },
      }))],
    })
    expect(lines).toEqual(['[click](https://x.test/a)'])
  })

  it('autolink marks (href == text) serialize as bare text', () => {
    const lines = expectRoundTrip({
      type: 'doc',
      content: [p(
        text('go to '),
        text('https://example.com', { type: 'link', attrs: { href: 'https://example.com' } }),
      )],
    })
    expect(lines).toEqual(['go to https://example.com'])
  })

  it('illustration atoms serialize to marker lines', () => {
    const lines = expectRoundTrip({
      type: 'doc',
      content: [p(text('before')), { type: 'illustration', attrs: { id: 'abc123' } }, p(text('after'))],
    })
    expect(lines).toEqual(['before', '', '⟦IMG:abc123⟧', '', 'after'])
  })

  it('inline code with backticks inside picks a longer fence', () => {
    const lines = expectRoundTrip({
      type: 'doc',
      content: [p(text('a `tick` b', { type: 'code' }))],
    })
    expect(lines).toEqual(['``a `tick` b``'])
  })

  it('code block containing a backtick fence', () => {
    expectRoundTrip({
      type: 'doc',
      content: [{
        type: 'codeBlock',
        attrs: { language: null },
        content: [text('```\nnested fence\n```')],
      }],
    })
  })

  it('empty doc serializes to no lines', () => {
    const lines = expectRoundTrip({ type: 'doc', content: [{ type: 'paragraph' }] })
    expect(lines).toEqual([])
  })
})

describe('tables', () => {
  it('system-box tables (LitRPG single column, padded separator)', () => {
    expectIdentity([
      'Prose before the box.',
      '',
      '| Mission Progress: Harvest Living Souls (1/10000) |',
      '| --- |',
      '',
      'Prose after.',
    ])
  })

  it('header-only tables (no body rows)', () => {
    expectIdentity([
      '| Ding! Check-in successful. Points +1! |',
      '| --- |',
    ])
  })

  it('multi-row system box', () => {
    expectIdentity([
      '| You successfully persuaded the royal house. |',
      '| --- |',
      '| You announced the first Dragon Gate Assembly. |',
      '| A year passes. |',
    ])
  })

  it('chatgroup tables (2-column, compact left-aligned separator)', () => {
    expectIdentity([
      '| Username | Message |',
      '|:---|:---|',
      '| Mr. Vampire | Anyone here? |',
      '| System | Member 【X】 has joined the Chat Group. |',
    ])
  })

  it('single-element table (rows joined by \\n, chatgroup storage) round-trips on joined text', () => {
    const lines = ['| Username | Message |\n|:---|:---|\n| A | hi |']
    const { doc, unsupported } = linesToDoc(lines)
    expect(unsupported).toEqual([])
    const warnings = []
    const out = docToLines(doc, warnings)
    expect(warnings).toEqual([])
    expect(out.join('\n')).toBe(lines.join('\n'))
  })

  it('center and right alignment', () => {
    expectIdentity([
      '| Stat | Value | Notes |',
      '| :---: | ---: | --- |',
      '| STR | 18 | buffed |',
    ])
  })

  it('escaped pipes inside cells', () => {
    expectIdentity([
      '| a | uses \\| pipe |',
      '| --- | --- |',
      '| `code \\| too` | ok |',
    ])
  })

  it('inline marks inside cells', () => {
    expectIdentity([
      '| Skill | Effect |',
      '| --- | --- |',
      '| **Fireball** | *burns* everything |',
    ])
  })

  it('editor-born table doc with TipTap default cell attrs', () => {
    const cell = (type, txt, attrs = {}) => ({
      type,
      attrs: { colspan: 1, rowspan: 1, colwidth: null, align: null, ...attrs },
      content: [txt ? p(text(txt)) : { type: 'paragraph' }],
    })
    const lines = expectRoundTrip({
      type: 'doc',
      content: [{
        type: 'table',
        content: [
          { type: 'tableRow', content: [cell('tableHeader', 'HP'), cell('tableHeader', 'MP')] },
          { type: 'tableRow', content: [cell('tableCell', '100'), cell('tableCell', '')] },
        ],
      }],
    })
    expect(lines).toEqual(['| HP | MP |', '| --- | --- |', '| 100 |  |'])
  })

  it('multi-paragraph cells flatten to one line with spaces', () => {
    const lines = expectRoundTrip({
      type: 'doc',
      content: [{
        type: 'table',
        content: [
          { type: 'tableRow', content: [{ type: 'tableHeader', content: [p(text('a')), p(text('b'))] }] },
          { type: 'tableRow', content: [{ type: 'tableCell', content: [p(text('c'), br, text('d'))] }] },
        ],
      }],
    })
    expect(lines).toEqual(['| a b |', '| --- |', '| c d |'])
  })

  it('merged cells block the save', () => {
    const { ok, warnings } = roundTrip({
      type: 'doc',
      content: [{
        type: 'table',
        content: [
          { type: 'tableRow', content: [{ type: 'tableHeader', attrs: { colspan: 2 }, content: [p(text('merged'))] }] },
          { type: 'tableRow', content: [{ type: 'tableCell', content: [p(text('a'))] }, { type: 'tableCell', content: [p(text('b'))] }] },
        ],
      }],
    })
    expect(ok).toBe(false)
    expect(warnings).toContain('table:merged-cells')
  })

  it('headerless tables block the save', () => {
    const { ok, warnings } = roundTrip({
      type: 'doc',
      content: [{
        type: 'table',
        content: [
          { type: 'tableRow', content: [{ type: 'tableCell', content: [p(text('no header'))] }] },
        ],
      }],
    })
    expect(ok).toBe(false)
    expect(warnings).toContain('table:structure')
  })
})

describe('unsupported content guards', () => {

  it('unknown node types trigger warnings on serialize', () => {
    const { ok, warnings } = roundTrip({
      type: 'doc',
      content: [{ type: 'mysteryBlock', content: [text('x')] }],
    })
    expect(ok).toBe(false)
    expect(warnings).toContain('node:mysteryBlock')
  })

  it('unknown marks trigger warnings on serialize', () => {
    const { ok, warnings } = roundTrip({
      type: 'doc',
      content: [p(text('u', { type: 'underline' }))],
    })
    expect(ok).toBe(false)
    expect(warnings).toContain('mark:underline')
  })
})

describe('normalizeDoc canonical form', () => {
  it('is idempotent', () => {
    const { doc } = linesToDoc([
      '## Head',
      '',
      'Some **bold** text.',
      'Second line.',
      '',
      '> quote',
    ])
    const once = normalizeDoc(doc)
    expect(normalizeDoc(once)).toEqual(once)
  })

  it('mark order does not affect equality', () => {
    const a = normalizeDoc({ type: 'doc', content: [p(text('x', { type: 'bold' }, { type: 'italic' }))] })
    const b = normalizeDoc({ type: 'doc', content: [p(text('x', { type: 'italic' }, { type: 'bold' }))] })
    expect(a).toEqual(b)
  })

  it('empty doc normalizes to no blocks', () => {
    expect(normalizeDoc({ type: 'doc', content: [{ type: 'paragraph' }] }).content).toEqual([])
  })
})
