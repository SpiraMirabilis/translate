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
