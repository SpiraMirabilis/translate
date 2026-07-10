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
    // Canonical nested form: 4-space child indent (python-markdown needs ≥4
    // to nest; marker-width indents flatten lists in EPUB/WordPress).
    expectIdentity([
      '- outer',
      '    - inner one',
      '    - inner two',
      '- outer two',
    ])
    // Legacy marker-width indents (2 spaces) still parse to the same doc and
    // re-serialize to the canonical 4-space form.
    const legacy = ['- outer', '  - inner one', '  - inner two', '- outer two']
    const { doc, unsupported } = linesToDoc(legacy)
    expect(unsupported).toEqual([])
    const warnings = []
    expect(docToLines(doc, warnings)).toEqual([
      '- outer',
      '    - inner one',
      '    - inner two',
      '- outer two',
    ])
    expect(warnings).toEqual([])
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
    expect(lines[0]).toContain('\\~\\~tildes\\~\\~') // every tilde escaped (delimiter-adjacency safety)
    expect(lines[0]).toContain('\\[brackets\\](x)')
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

  it('multi-paragraph / hard-break cells switch the table to sentinel form', () => {
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
    expect(lines).toEqual([
      '⟦TABLE⟧',
      '⟦TR⟧', '⟦TH⟧', 'a', '', 'b', '⟦/TH⟧', '⟦/TR⟧',
      '⟦TR⟧', '⟦TD⟧', 'c', 'd', '⟦/TD⟧', '⟦/TR⟧',
      '⟦/TABLE⟧',
    ])
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

  it('fully headerless tables coerce their first row to a header transparently', () => {
    const cell = (t) => ({ type: 'tableCell', content: [p(text(t))] })
    const lines = expectRoundTrip({
      type: 'doc',
      content: [{
        type: 'table',
        content: [
          { type: 'tableRow', content: [cell('Name'), cell('HP')] },
          { type: 'tableRow', content: [cell('Slime'), cell('10')] },
        ],
      }],
    })
    expect(lines).toEqual(['| Name | HP |', '| --- | --- |', '| Slime | 10 |'])
    // Single headerless row → header-only table (both corpus-valid).
    const solo = expectRoundTrip({
      type: 'doc',
      content: [{
        type: 'table',
        content: [{ type: 'tableRow', content: [cell('only')] }],
      }],
    })
    expect(solo).toEqual(['| only |', '| --- |'])
  })

  it('header cells outside the first row still block the save', () => {
    const { ok, warnings } = roundTrip({
      type: 'doc',
      content: [{
        type: 'table',
        content: [
          { type: 'tableRow', content: [{ type: 'tableHeader', content: [p(text('h'))] }] },
          { type: 'tableRow', content: [{ type: 'tableHeader', content: [p(text('another header'))] }] },
        ],
      }],
    })
    expect(ok).toBe(false)
    expect(warnings).toContain('table:structure')
  })
})

describe('sentinel (rich) tables', () => {
  const RICH_LINES = [
    'Prose before.',
    '',
    '⟦TABLE⟧',
    '⟦TR⟧',
    '⟦TH:center⟧', 'Stat', '⟦/TH⟧',
    '⟦TH⟧', 'Value', '⟦/TH⟧',
    '⟦/TR⟧',
    '⟦TR⟧',
    '⟦TD⟧', 'HP: 100', 'MP: 50', '', '- Fireball', '- Ice Lance', '⟦/TD⟧',
    '⟦TD⟧', '⟦/TD⟧',
    '⟦/TR⟧',
    '⟦/TABLE⟧',
    '',
    'Prose after.',
  ]

  it('stored sentinel tables round-trip byte-identically', () => {
    expectIdentity(RICH_LINES)
  })

  it('parses into a table node with block cell content and align', () => {
    const { doc, unsupported } = linesToDoc(RICH_LINES)
    expect(unsupported).toEqual([])
    const table = doc.content.find((b) => b.type === 'table')
    const [headRow, bodyRow] = table.content
    expect(headRow.content[0].type).toBe('tableHeader')
    expect(headRow.content[0].attrs.align).toBe('center')
    const richCell = bodyRow.content[0]
    expect(richCell.content.map((b) => b.type)).toEqual(['paragraph', 'bulletList'])
    expect(richCell.content[0].content.some((n) => n.type === 'hardBreak')).toBe(true)
    // empty cell → single empty paragraph
    expect(bodyRow.content[1].content).toEqual([{ type: 'paragraph' }])
  })

  it('blockquotes and multiple paragraphs in cells round-trip', () => {
    expectIdentity([
      '⟦TABLE⟧',
      '⟦TR⟧', '⟦TH⟧', 'h', '⟦/TH⟧', '⟦/TR⟧',
      '⟦TR⟧', '⟦TD⟧', 'first para', '', '> quoted', '', 'second para', '⟦/TD⟧', '⟦/TR⟧',
      '⟦/TABLE⟧',
    ])
  })

  it('editor-born rich cell serializes to sentinel form and degrades back to pipe', () => {
    const doc = {
      type: 'doc',
      content: [{
        type: 'table',
        content: [
          { type: 'tableRow', content: [{ type: 'tableHeader', content: [p(text('h'))] }] },
          { type: 'tableRow', content: [{ type: 'tableCell', content: [p(text('a'), br, text('b'))] }] },
        ],
      }],
    }
    const lines = expectRoundTrip(doc)
    expect(lines[0]).toBe('⟦TABLE⟧')
    // Remove the hard break → simple again → pipe form, byte-identical emitter
    const simple = {
      type: 'doc',
      content: [{
        type: 'table',
        content: [
          { type: 'tableRow', content: [{ type: 'tableHeader', content: [p(text('h'))] }] },
          { type: 'tableRow', content: [{ type: 'tableCell', content: [p(text('a b'))] }] },
        ],
      }],
    }
    expect(expectRoundTrip(simple)).toEqual(['| h |', '| --- |', '| a b |'])
  })

  it('inline marks inside rich cells round-trip', () => {
    expectIdentity([
      '⟦TABLE⟧',
      '⟦TR⟧', '⟦TH⟧', 'h', '⟦/TH⟧', '⟦/TR⟧',
      '⟦TR⟧', '⟦TD⟧', '**bold** and *italic*', 'and `code`', '⟦/TD⟧', '⟦/TR⟧',
      '⟦/TABLE⟧',
    ])
  })

  it('disallowed block types in cells block the load', () => {
    const { unsupported } = linesToDoc([
      '⟦TABLE⟧',
      '⟦TR⟧', '⟦TH⟧', '# heading in cell', '⟦/TH⟧', '⟦/TR⟧',
      '⟦/TABLE⟧',
    ])
    expect(unsupported).toContain('table-cell:heading')
  })

  it('disallowed block types in cells block the save', () => {
    const { ok, warnings } = roundTrip({
      type: 'doc',
      content: [{
        type: 'table',
        content: [{
          type: 'tableRow',
          content: [{ type: 'tableHeader', content: [{ type: 'heading', attrs: { level: 1 }, content: [text('h')] }] }],
        }],
      }],
    })
    expect(ok).toBe(false)
    expect(warnings).toContain('table-cell:heading')
  })

  it('malformed sentinel runs load read-only (table:malformed)', () => {
    const { unsupported } = linesToDoc(['⟦TABLE⟧', '⟦TR⟧', 'no cell marker'])
    expect(unsupported).toContain('table:malformed')
  })

  it('literal marker text in a cell blocks the save', () => {
    const { ok, warnings } = roundTrip({
      type: 'doc',
      content: [{
        type: 'table',
        content: [{
          type: 'tableRow',
          content: [{ type: 'tableHeader', content: [p(text('⟦/TD⟧')), p(text('x'))] }],
        }],
      }],
    })
    expect(ok).toBe(false)
    expect(warnings).toContain('table-cell:marker-literal')
  })

  it('literal marker text in a plain paragraph blocks the save', () => {
    const { ok, warnings } = roundTrip({
      type: 'doc',
      content: [p(text('⟦TABLE⟧'))],
    })
    expect(ok).toBe(false)
    expect(warnings).toContain('sentinel:literal')
  })

  it('align canonicalizes off the header row in sentinel form', () => {
    const cell = (type, txt, align = null) => ({
      type, attrs: { align }, content: [p(text(txt))],
    })
    const lines = expectRoundTrip({
      type: 'doc',
      content: [{
        type: 'table',
        content: [
          { type: 'tableRow', content: [cell('tableHeader', 'h', 'right')] },
          {
            type: 'tableRow',
            content: [{ type: 'tableCell', attrs: { align: null }, content: [p(text('a'), br, text('b'))] }],
          },
        ],
      }],
    })
    expect(lines).toContain('⟦TH:right⟧')
    expect(lines).toContain('⟦TD⟧') // body cell: align never emitted
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
      content: [p(text('h', { type: 'highlight' }))],
    })
    expect(ok).toBe(false)
    expect(warnings).toContain('mark:highlight')
  })
})

describe('underline and color (inline ⟦⟧ sentinels)', () => {
  const u = { type: 'underline' }
  const color = (c) => ({ type: 'textStyle', attrs: { color: c } })

  it('stored marker pairs round-trip byte-identically', () => {
    expectIdentity([
      'Plain then ⟦U⟧underlined⟦/U⟧ then plain.',
      '',
      'A ⟦COLOR:#ff0000⟧red word⟦/COLOR⟧ here.',
      '',
      '⟦COLOR:#00aaff⟧⟦U⟧both⟦/U⟧⟦/COLOR⟧ at once.',
    ])
  })

  it('editor-born underline and color serialize to markers', () => {
    expect(expectRoundTrip({
      type: 'doc',
      content: [p(text('a '), text('u', u), text(' b'))],
    })).toEqual(['a ⟦U⟧u⟦/U⟧ b'])
    expect(expectRoundTrip({
      type: 'doc',
      content: [p(text('x', color('#ff0000')))],
    })).toEqual(['⟦COLOR:#ff0000⟧x⟦/COLOR⟧'])
  })

  it('combines with markdown marks', () => {
    expectIdentity(['⟦U⟧**bold under**⟦/U⟧ and ⟦COLOR:#112233⟧*tinted italic*⟦/COLOR⟧'])
    const lines = expectRoundTrip({
      type: 'doc',
      content: [p(text('bu', { type: 'bold' }, u))],
    })
    expect(lines).toEqual(['⟦U⟧**bu**⟦/U⟧'])
  })

  it('a pair may span a code mark', () => {
    expectIdentity(['⟦U⟧a `code` b⟦/U⟧'])
  })

  it('markers inside code spans stay literal', () => {
    expectIdentity(['`⟦U⟧ literal ⟦/U⟧`'])
    const { doc } = linesToDoc(['`⟦U⟧ literal ⟦/U⟧`'])
    const codeNode = doc.content[0].content[0]
    expect(codeNode.marks).toEqual([{ type: 'code' }])
    expect(codeNode.text).toBe('⟦U⟧ literal ⟦/U⟧')
  })

  it('color normalization: #RGB and rgb() → #rrggbb, invalid dropped', () => {
    expect(expectRoundTrip({
      type: 'doc',
      content: [p(text('x', color('#A1C')))],
    })).toEqual(['⟦COLOR:#aa11cc⟧x⟦/COLOR⟧'])
    expect(expectRoundTrip({
      type: 'doc',
      content: [p(text('x', color('rgb(255, 0, 128)')))],
    })).toEqual(['⟦COLOR:#ff0080⟧x⟦/COLOR⟧'])
    // Unparseable color: mark carries no canonical form → plain text
    expect(expectRoundTrip({
      type: 'doc',
      content: [p(text('x', color('salmon')))],
    })).toEqual(['x'])
  })

  it('underline over a hard break closes and reopens per line', () => {
    const lines = expectRoundTrip({
      type: 'doc',
      content: [p(text('one', u), { type: 'hardBreak' }, text('two', u))],
    })
    expect(lines).toEqual(['⟦U⟧one⟦/U⟧', '⟦U⟧two⟦/U⟧'])
  })

  it('adjacent different colors emit separate pairs', () => {
    const lines = expectRoundTrip({
      type: 'doc',
      content: [p(text('red', color('#ff0000')), text('blue', color('#0000ff')))],
    })
    expect(lines).toEqual(['⟦COLOR:#ff0000⟧red⟦/COLOR⟧⟦COLOR:#0000ff⟧blue⟦/COLOR⟧'])
  })

  it('underline/color work inside table cells (pipe and sentinel)', () => {
    expectIdentity([
      '| h |',
      '| --- |',
      '| ⟦U⟧cell⟦/U⟧ |',
    ])
    expectIdentity([
      '⟦TABLE⟧',
      '⟦TR⟧', '⟦TH⟧', 'h', '⟦/TH⟧', '⟦/TR⟧',
      '⟦TR⟧', '⟦TD⟧', '⟦COLOR:#22cc88⟧HP⟦/COLOR⟧: 100', 'MP: 50', '⟦/TD⟧', '⟦/TR⟧',
      '⟦/TABLE⟧',
    ])
  })

  it('underline in headings and blockquotes round-trips', () => {
    expectIdentity(['# ⟦U⟧Title⟦/U⟧', '', '> ⟦COLOR:#993300⟧warm⟦/COLOR⟧ quote'])
  })

  it('literal/unmatched marker text blocks the save', () => {
    const { doc } = linesToDoc(['stray ⟦U⟧ marker without close'])
    const rt = roundTrip(doc)
    expect(rt.ok).toBe(false)
    expect(rt.warnings).toContain('sentinel:literal')
    // misnested pairs also stay literal → blocked
    const { doc: doc2 } = linesToDoc(['⟦U⟧a⟦COLOR:#112233⟧b⟦/U⟧c⟦/COLOR⟧'])
    const rt2 = roundTrip(doc2)
    expect(rt2.ok).toBe(false)
    expect(rt2.warnings).toContain('sentinel:literal')
  })

  it('underlined bare URL round-trips (autolink + marker interplay)', () => {
    expectIdentity(['see ⟦U⟧https://example.com/path⟦/U⟧ now'])
  })

  it('whitespace at underline edges is preserved (no flanking rules)', () => {
    expectIdentity(['a⟦U⟧ padded ⟦/U⟧b'])
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

describe('non-breaking space canonicalization (Grammarly paste artifacts)', () => {
  // markdown-it treats U+00A0 as whitespace: emphasis can't open/close against
  // it, and an nbsp-only line parses to an empty paragraph. isWs must agree or
  // roundTrip() blocks saves on content the user can't even see.
  it('bold with a trailing nbsp inside the mark round-trips', () => {
    expectRoundTrip({
      type: 'doc',
      content: [p(text('alpha\u00A0', { type: 'bold' }), text('beta'))],
    })
  })

  it('an nbsp-only paragraph normalizes away instead of blocking the save', () => {
    const lines = expectRoundTrip({
      type: 'doc',
      content: [p(text('before')), p(text('\u00A0')), p(text('after'))],
    })
    expect(lines).toEqual(['before', '', 'after'])
  })

  it('interior nbsp between visible characters is preserved', () => {
    const lines = expectRoundTrip({
      type: 'doc',
      content: [p(text('a\u00A0b'))],
    })
    expect(lines).toEqual(['a\u00A0b'])
    expectIdentity(lines)
  })

  it('nbsp-padded emphasis at line edges round-trips', () => {
    expectRoundTrip({
      type: 'doc',
      content: [p(text('\u00A0lead', { type: 'italic' }), br, text('\u00A0'), text('tail\u00A0', { type: 'strike' }))],
    })
  })
})

describe('flanking/escaping asymmetries (fuzzer-found regressions)', () => {
  // Each case here once made roundTrip() block a save. The classes:
  // CommonMark emphasis flanking, delimiter-run merging, span-local escaping,
  // and JS-vs-markdown whitespace semantics.
  it('strike content starting with a tilde', () => {
    expectRoundTrip({ type: 'doc', content: [p(text('~b', { type: 'strike' }))] })
  })

  it('emphasis on punctuation flush against a word char (flanking)', () => {
    // opener can't sit between word char and punctuation: "b*#*"
    expectRoundTrip({ type: 'doc', content: [p(text('b'), text('#', { type: 'italic' }))] })
    // closer can't sit between punctuation and word char: "*x!*s"
    expectRoundTrip({ type: 'doc', content: [p(text('x!', { type: 'italic' }), text('s'))] })
  })

  it('trailing backslash flush against a mark delimiter', () => {
    expectRoundTrip({ type: 'doc', content: [p(text('\\'), text('x', { type: 'strike' }))] })
  })

  it('closing bracket inside a link label', () => {
    expectRoundTrip({
      type: 'doc',
      content: [p(text('a]b', { type: 'link', attrs: { href: 'https://x.example/p' } }))],
    })
  })

  it('footnote refs stay byte-exact despite bracket escaping', () => {
    expectIdentity(['He waved[1] and left.', '', '[1] A cultural note.'])
  })

  it('overlapping (non-nested) bold/italic ranges with edge whitespace', () => {
    // italic covers first two nodes, bold covers last two — the crossing
    // splits bold; whitespace at the split point must be expelled.
    expectRoundTrip({
      type: 'doc',
      content: [p(
        text('alpha', { type: 'italic' }),
        text('beta', { type: 'bold' }, { type: 'italic' }),
        text(' gamma', { type: 'bold' }),
      )],
    })
  })

  it('nested italic inside bold keeps its surrounding spaces', () => {
    // Regression guard for the overlap fix: nesting must NOT expel spaces.
    expectIdentity(['Nested **bold with *italic* inside** it.'])
  })

  it('strike nested in italic followed by a non-space word char', () => {
    expectRoundTrip({
      type: 'doc',
      content: [p(text('Q', { type: 'italic' }, { type: 'strike' }), text('s'))],
    })
  })

  it('autolink-looking text inside emphasis does not desync (linkify)', () => {
    expectRoundTrip({
      type: 'doc',
      content: [p(text('see r.it now', { type: 'bold' }, { type: 'italic' }))],
    })
  })
})

describe('line-terminator characters (JS vs markdown semantics)', () => {
  const LS = String.fromCodePoint(0x2028)

  it('U+2028-only paragraph normalizes away instead of blocking', () => {
    const rt = roundTrip({ type: 'doc', content: [p(text('a')), p(text(LS))] })
    expect(rt.ok).toBe(true)
  })

  it('U+2028 at a line edge is expelled; interior stays literal', () => {
    expectRoundTrip({ type: 'doc', content: [p(text(`tail${LS}`))] })
    expectRoundTrip({ type: 'doc', content: [p(text(`a${LS}b`))] })
  })

  it('raw newline in a text node becomes a line break', () => {
    const rt = roundTrip({ type: 'doc', content: [p(text('a\nb'))] })
    expect(rt.ok).toBe(true)
    expect(rt.lines).toEqual(['a', 'b'])
  })

  it('U+2028 inside a code span becomes a space (pad-strip regex)', () => {
    expectRoundTrip({ type: 'doc', content: [p(text(`${LS}\``, { type: 'code' }))] })
  })
})
