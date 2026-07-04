/**
 * @vitest-environment jsdom
 *
 * pasteCleanup — clipboard hygiene for the write editor. Fixtures mirror what
 * Grammarly/Word/Google Docs actually put on the clipboard: filler paragraphs
 * between real ones and non-breaking spaces inside prose.
 */
import { describe, it, expect } from 'vitest'
import { cleanPastedHTML, cleanPastedText } from './pasteCleanup'

describe('cleanPastedHTML', () => {
  it('drops nbsp/br filler paragraphs and normalizes nbsp (Grammarly shape)', () => {
    const html = '<p>a</p><p>&nbsp;</p><p><br></p><p>b\u00A0c</p>'
    expect(cleanPastedHTML(html)).toBe('<p>a</p><p>b c</p>')
  })

  it('drops empty divs and whitespace-only blocks, nested fillers first', () => {
    const html = '<div><p>keep</p><p>  </p></div><div>\u00A0</div><p>also keep</p>'
    expect(cleanPastedHTML(html)).toBe('<div><p>keep</p></div><p>also keep</p>')
  })

  it('keeps textless blocks that carry structure (img, hr, table)', () => {
    const html = '<p><img src="x.png"></p><div><hr></div><p>&nbsp;</p>'
    expect(cleanPastedHTML(html)).toBe('<p><img src="x.png"></p><div><hr></div>')
  })

  it('preserves inline formatting untouched', () => {
    const html = '<p><strong>bold</strong> and <em>italic</em></p>'
    expect(cleanPastedHTML(html)).toBe(html)
  })

  it('promotes a headerless pasted table’s first row to header cells', () => {
    const html = '<table><tbody><tr><td>Name</td><td>HP</td></tr><tr><td>Slime</td><td>10</td></tr></tbody></table>'
    const out = cleanPastedHTML(html)
    expect(out).toContain('<th>Name</th><th>HP</th>')
    expect(out).toContain('<td>Slime</td><td>10</td>')
    // A table that already has a header row is untouched.
    const withHeader = '<table><tbody><tr><th>A</th></tr><tr><td>1</td></tr></tbody></table>'
    expect(cleanPastedHTML(withHeader)).toBe(withHeader)
  })

  it('normalizes nbsp inside formatted runs', () => {
    const html = '<p><strong>bold\u00A0edge</strong></p>'
    expect(cleanPastedHTML(html)).toBe('<p><strong>bold edge</strong></p>')
  })
})

describe('cleanPastedText', () => {
  it('replaces every nbsp with a plain space', () => {
    expect(cleanPastedText('a\u00A0b\u00A0\u00A0c')).toBe('a b  c')
  })

  it('leaves normal text alone', () => {
    expect(cleanPastedText('plain text')).toBe('plain text')
  })
})
