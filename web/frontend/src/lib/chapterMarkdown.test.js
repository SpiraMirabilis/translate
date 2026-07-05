/**
 * @vitest-environment jsdom
 *
 * chapterMarkdown — segment splitting around ⟦IMG:id⟧ markers, footnote
 * parsing/marking/linkifying, and sanitized Markdown rendering (DOMPurify
 * needs a DOM, hence jsdom).
 */
import { describe, it, expect } from 'vitest'
import {
  markerId, splitSegments, parseFootnotes, markFootnoteLine,
  markFootnoteRefs, linkifyFootnotes, renderInline, renderBlock,
  parseTableRun, renderTable, renderSegment, TABLE_MARKER_RE,
  replaceInlineSentinels,
} from './chapterMarkdown'

describe('markerId', () => {
  it('matches whole-line ⟦IMG:hex⟧ markers (with surrounding whitespace)', () => {
    expect(markerId('⟦IMG:abcd⟧')).toBe('abcd')
    expect(markerId('  ⟦IMG:0f1e2d⟧  ')).toBe('0f1e2d')
  })

  it('rejects non-markers, short ids, uppercase hex, and inline markers', () => {
    expect(markerId('plain text')).toBeNull()
    expect(markerId('⟦IMG:abc⟧')).toBeNull()          // < 4 hex chars
    expect(markerId('⟦IMG:ABCD⟧')).toBeNull()         // uppercase not allowed
    expect(markerId('before ⟦IMG:abcd⟧ after')).toBeNull() // not whole-line
    expect(markerId(42)).toBeNull()
    expect(markerId(null)).toBeNull()
  })
})

describe('splitSegments', () => {
  it('splits line arrays into text runs and img segments in order', () => {
    const lines = ['one', 'two', '⟦IMG:beef⟧', 'three', '⟦IMG:cafe⟧']
    expect(splitSegments(lines)).toEqual([
      { type: 'text', md: 'one\ntwo' },
      { type: 'img', id: 'beef' },
      { type: 'text', md: 'three' },
      { type: 'img', id: 'cafe' },
    ])
  })

  it('keeps blank lines inside a run (paragraph separators)', () => {
    expect(splitSegments(['a', '', 'b'])).toEqual([
      { type: 'text', md: 'a\n\nb' },
    ])
  })

  it('handles empty/absent input and marker-only arrays', () => {
    expect(splitSegments([])).toEqual([])
    expect(splitSegments(undefined)).toEqual([])
    expect(splitSegments(['⟦IMG:abcd⟧'])).toEqual([{ type: 'img', id: 'abcd' }])
  })
})

// A well-formed rich table used across the suites below.
const TABLE_LINES = [
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
]

describe('parseTableRun', () => {
  it('parses a rich table into rows/cells with header, align, and lines', () => {
    const res = parseTableRun(TABLE_LINES, 0)
    expect(res.next).toBe(TABLE_LINES.length)
    expect(res.rows).toEqual([
      { cells: [
        { header: true, align: 'center', lines: ['Stat'] },
        { header: true, align: null, lines: ['Value'] },
      ] },
      { cells: [
        { header: false, align: null, lines: ['HP: 100', 'MP: 50', '', '- Fireball', '- Ice Lance'] },
        { header: false, align: null, lines: [] },
      ] },
    ])
  })

  it('tolerates surrounding whitespace on marker lines', () => {
    const res = parseTableRun(['  ⟦TABLE⟧ ', ' ⟦TR⟧', '⟦TD⟧ ', 'x', ' ⟦/TD⟧', '⟦/TR⟧ ', ' ⟦/TABLE⟧'], 0)
    expect(res).not.toBeNull()
    expect(res.rows[0].cells[0].lines).toEqual(['x'])
  })

  it('rejects malformed runs', () => {
    const cases = [
      ['⟦TABLE⟧', '⟦TR⟧', '⟦TD⟧', 'x', '⟦/TD⟧', '⟦/TR⟧'],            // EOF, no close
      ['⟦TABLE⟧', '⟦TR⟧', '⟦TH⟧', 'x', '⟦/TD⟧', '⟦/TR⟧', '⟦/TABLE⟧'], // mismatched cell close
      ['⟦TABLE⟧', '⟦/TABLE⟧'],                                        // empty table
      ['⟦TABLE⟧', '⟦TR⟧', '⟦/TR⟧', '⟦/TABLE⟧'],                       // empty row
      ['⟦TABLE⟧', 'prose', '⟦/TABLE⟧'],                               // prose outside cell
      ['⟦TABLE⟧', '⟦TR⟧', '⟦TD⟧', '⟦TABLE⟧', '⟦/TD⟧', '⟦/TR⟧', '⟦/TABLE⟧'], // nested table
      ['⟦TABLE⟧', '⟦TR⟧', '⟦TD⟧', '⟦IMG:abcd⟧', '⟦/TD⟧', '⟦/TR⟧', '⟦/TABLE⟧'], // img in cell
      ['⟦TABLE⟧', '⟦TR⟧', '⟦TD⟧', 'x', '⟦/TD⟧', '⟦/TABLE⟧'],          // missing ⟦/TR⟧
    ]
    for (const c of cases) expect(parseTableRun(c, 0)).toBeNull()
  })

  it('accepts (and ignores semantically) align on TD markers', () => {
    const res = parseTableRun(['⟦TABLE⟧', '⟦TR⟧', '⟦TD:right⟧', 'x', '⟦/TD⟧', '⟦/TR⟧', '⟦/TABLE⟧'], 0)
    expect(res.rows[0].cells[0].align).toBe('right')
  })
})

describe('splitSegments — sentinel tables', () => {
  it('emits table segments between text runs', () => {
    const lines = ['before', ...TABLE_LINES, 'after']
    const segs = splitSegments(lines)
    expect(segs.map((s) => s.type)).toEqual(['text', 'table', 'text'])
    expect(segs[1].rows).toHaveLength(2)
  })

  it('interleaves with img markers', () => {
    const segs = splitSegments(['⟦IMG:abcd⟧', ...TABLE_LINES])
    expect(segs.map((s) => s.type)).toEqual(['img', 'table'])
  })

  it('downgrades a malformed run to literal text with tableError', () => {
    const segs = splitSegments(['⟦TABLE⟧', '⟦TR⟧', 'no cells here', 'more text'])
    expect(segs).toHaveLength(1)
    expect(segs[0].type).toBe('text')
    expect(segs[0].tableError).toBe(true)
    expect(segs[0].md).toContain('⟦TABLE⟧')
  })

  it('leaves orphan close markers as plain text without tableError', () => {
    const segs = splitSegments(['some text', '⟦/TD⟧'])
    expect(segs).toEqual([{ type: 'text', md: 'some text\n⟦/TD⟧' }])
  })
})

describe('renderTable', () => {
  const seg = () => splitSegments(TABLE_LINES)[0]

  it('renders header row in thead with align attr, body in tbody', () => {
    const html = renderTable(seg())
    expect(html).toContain('<thead>')
    expect(html).toContain('<th align="center">')
    expect(html).toContain('<tbody>')
    expect(html.indexOf('<thead>')).toBeLessThan(html.indexOf('<tbody>'))
  })

  it('renders block content inside cells (breaks, lists, empty cell)', () => {
    const html = renderTable(seg())
    expect(html).toContain('<br>')          // HP/MP adjacent lines
    expect(html).toContain('<li>Fireball</li>')
    expect(html).toMatch(/<td><\/td>/)       // empty cell
  })

  it('omits tbody for header-only tables', () => {
    const res = parseTableRun(['⟦TABLE⟧', '⟦TR⟧', '⟦TH⟧', 'only', '⟦/TH⟧', '⟦/TR⟧', '⟦/TABLE⟧'], 0)
    const html = renderTable({ rows: res.rows })
    expect(html).toContain('<thead>')
    expect(html).not.toContain('<tbody>')
  })

  it('sanitizes XSS in cell content', () => {
    const res = parseTableRun(
      ['⟦TABLE⟧', '⟦TR⟧', '⟦TD⟧', '<script>alert(1)</script>', '⟦/TD⟧', '⟦/TR⟧', '⟦/TABLE⟧'], 0)
    const html = renderTable({ rows: res.rows })
    expect(html).not.toContain('<script')
  })

  it('lets footnote sentinels ride through as text', () => {
    const res = parseTableRun(['⟦TABLE⟧', '⟦TR⟧', '⟦TD⟧', 'see⟦FN:1⟧', '⟦/TD⟧', '⟦/TR⟧', '⟦/TABLE⟧'], 0)
    expect(renderTable({ rows: res.rows })).toContain('⟦FN:1⟧')
  })
})

describe('renderSegment', () => {
  it('dispatches text vs table segments', () => {
    expect(renderSegment({ type: 'text', md: '**b**' })).toContain('<strong>b</strong>')
    expect(renderSegment(splitSegments(TABLE_LINES)[0])).toContain('<table>')
  })
})

describe('inline sentinels (⟦U⟧ / ⟦COLOR:#hex⟧)', () => {
  it('renders underline and color pairs in block markdown', () => {
    expect(renderBlock('a ⟦U⟧under⟦/U⟧ b')).toContain('<u>under</u>')
    expect(renderBlock('x ⟦COLOR:#ff0000⟧red⟦/COLOR⟧ y'))
      .toContain('<span style="color:#ff0000">red</span>')
  })

  it('renders in inline mode and inside table cells', () => {
    expect(renderInline('⟦U⟧u⟦/U⟧')).toContain('<u>u</u>')
    const res = parseTableRun(
      ['⟦TABLE⟧', '⟦TR⟧', '⟦TD⟧', '⟦COLOR:#00ff00⟧hp⟦/COLOR⟧', '⟦/TD⟧', '⟦/TR⟧', '⟦/TABLE⟧'], 0)
    expect(renderTable({ rows: res.rows })).toContain('<span style="color:#00ff00">hp</span>')
  })

  it('nests with markdown marks', () => {
    const html = renderBlock('⟦U⟧**bold under**⟦/U⟧')
    expect(html).toContain('<u><strong>bold under</strong></u>')
  })

  it('leaves unmatched, misnested, and invalid markers literal', () => {
    expect(renderBlock('lonely ⟦U⟧ marker')).toContain('⟦U⟧')
    expect(renderBlock('bad ⟦/COLOR⟧ close')).toContain('⟦/COLOR⟧')
    expect(renderBlock('⟦COLOR⟧no hex⟦/COLOR⟧')).toContain('⟦COLOR⟧')
    expect(renderBlock('⟦COLOR:red⟧named⟦/COLOR⟧')).toContain('⟦COLOR:red⟧')
    // misnested: U closed while COLOR is innermost
    const html = renderBlock('⟦U⟧a⟦COLOR:#112233⟧b⟦/U⟧c⟦/COLOR⟧')
    expect(html).not.toContain('<u>')
    expect(html).toContain('<span style="color:#112233">')
  })

  it('keeps markers inside code spans literal', () => {
    const html = renderBlock('`⟦U⟧ in code ⟦/U⟧`')
    expect(html).not.toContain('<u>')
    expect(html).toContain('⟦U⟧ in code ⟦/U⟧')
  })

  it('a pair may span a whole code element', () => {
    const html = renderBlock('⟦U⟧a `code` b⟦/U⟧')
    expect(html).toContain('<u>a <code>code</code> b</u>')
  })

  it('never pairs across block boundaries', () => {
    const html = renderBlock('⟦U⟧first\n\nsecond⟦/U⟧')
    expect(html).not.toContain('<u>')
    expect(html).toContain('⟦U⟧')
  })

  it('replaceInlineSentinels supports custom tags (bbcode)', () => {
    const out = replaceInlineSentinels('⟦U⟧x⟦/U⟧ ⟦COLOR:#abcdef⟧y⟦/COLOR⟧', {
      uOpen: '[U]', uClose: '[/U]', colorOpen: (h) => `[COLOR=${h}]`, colorClose: '[/COLOR]',
    })
    expect(out).toBe('[U]x[/U] [COLOR=#abcdef]y[/COLOR]')
  })
})

describe('TABLE_MARKER_RE', () => {
  it('matches all marker forms and rejects lookalikes', () => {
    for (const m of ['⟦TABLE⟧', '⟦/TABLE⟧', '⟦TR⟧', '⟦/TR⟧', '⟦TH⟧', '⟦TH:left⟧', '⟦TD:center⟧', '⟦/TD⟧']) {
      expect(TABLE_MARKER_RE.test(m)).toBe(true)
    }
    for (const m of ['⟦TABLE', 'x ⟦TR⟧', '⟦TH:middle⟧', '⟦IMG:abcd⟧', '[TABLE]']) {
      expect(TABLE_MARKER_RE.test(m)).toBe(false)
    }
  })
})

describe('parseFootnotes', () => {
  it('collects [n] definition lines into map + ids', () => {
    const { map, ids } = parseFootnotes([
      'Body text with a ref[1] inline.',
      '[1] First note',
      '[2] — Second note with dash',
    ])
    expect(map).toEqual({ 1: 'First note', 2: 'Second note with dash' })
    expect(ids).toEqual(new Set(['1', '2']))
  })

  it('ignores inline refs, non-strings, and empty input', () => {
    const { map, ids } = parseFootnotes(['word[3] mid-line', null, 42])
    expect(map).toEqual({})
    expect(ids.size).toBe(0)
    expect(parseFootnotes(undefined).ids.size).toBe(0)
  })
})

describe('markFootnoteLine / markFootnoteRefs', () => {
  const ids = new Set(['1', '2'])

  it('replaces known inline refs with sentinels', () => {
    expect(markFootnoteLine('hello[1] world[2]', ids)).toBe('hello⟦FN:1⟧ world⟦FN:2⟧')
  })

  it('leaves unknown refs and definition lines untouched', () => {
    expect(markFootnoteLine('hello[9] world', ids)).toBe('hello[9] world')
    expect(markFootnoteLine('[1] definition text', ids)).toBe('[1] definition text')
  })

  it('is a no-op when there are no footnote ids', () => {
    expect(markFootnoteLine('hello[1]', new Set())).toBe('hello[1]')
    expect(markFootnoteRefs(['a[1]'], new Set())).toEqual(['a[1]'])
  })

  it('maps across a line array preserving indices', () => {
    expect(markFootnoteRefs(['a[1]', '', '[2] def'], ids))
      .toEqual(['a⟦FN:1⟧', '', '[2] def'])
  })
})

describe('linkifyFootnotes', () => {
  it('swaps sentinels for footnote-ref anchors', () => {
    expect(linkifyFootnotes('x ⟦FN:3⟧ y')).toBe(
      'x <a class="footnote-ref" data-fn="3" role="button" tabindex="0">[3]</a> y'
    )
  })

  it('passes through empty/plain html', () => {
    expect(linkifyFootnotes('')).toBe('')
    expect(linkifyFootnotes('<p>hi</p>')).toBe('<p>hi</p>')
  })
})

describe('renderInline', () => {
  it('renders inline markdown', () => {
    expect(renderInline('**bold** and *em*')).toBe('<strong>bold</strong> and <em>em</em>')
  })

  it('returns empty string for blank input', () => {
    expect(renderInline('')).toBe('')
    expect(renderInline('   ')).toBe('')
  })

  it('escapes raw HTML (XSS)', () => {
    const out = renderInline('<script>alert(1)</script>')
    expect(out).not.toContain('<script')
    expect(out).toContain('&lt;script&gt;')
    // The payload survives only as escaped text, never as an element
    const img = renderInline('<img src=x onerror=alert(1)>')
    expect(img).not.toContain('<img')
    expect(img).toContain('&lt;img')
  })

  it('never emits javascript: links', () => {
    // markdown-it's validateLink rejects the scheme → stays literal text
    const out = renderInline('[click](javascript:alert(1))')
    expect(out).not.toContain('<a')
    expect(out).not.toMatch(/href="javascript:/i)
  })

  it('hardens legit links with target/rel', () => {
    const out = renderInline('[site](https://example.com)')
    expect(out).toContain('href="https://example.com"')
    expect(out).toContain('target="_blank"')
    expect(out).toContain('rel="nofollow noopener noreferrer"')
  })
})

describe('renderBlock', () => {
  it('renders block-level markdown (lists, paragraphs)', () => {
    const out = renderBlock('- one\n- two')
    expect(out).toContain('<ul>')
    expect(out).toContain('<li>one</li>')
    expect(renderBlock('a\n\nb')).toContain('<p>a</p>')
  })

  it('turns single newlines within a block into <br>', () => {
    expect(renderBlock('line1\nline2')).toContain('<br>')
  })

  it('disables markdown image syntax', () => {
    expect(renderBlock('![alt](https://example.com/x.png)')).not.toContain('<img')
  })

  it('sanitizes raw HTML (XSS)', () => {
    const out = renderBlock('hello\n\n<script>alert(1)</script>\n\n<iframe src="https://evil"></iframe>')
    expect(out).not.toContain('<script')
    expect(out).not.toContain('<iframe')
  })
})
