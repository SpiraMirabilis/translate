// Paste hygiene for the write editor. Rich-text sources (Grammarly, Word,
// Google Docs) separate paragraphs with filler blocks — <p>&nbsp;</p>,
// <p><br></p>, empty <div>s — and pepper text with non-breaking spaces.
// TipTap would keep the fillers as empty paragraphs, and U+00A0 survives all
// the way into saved markdown lines (it even breaks emphasis reparsing, which
// blocks saves via the round-trip guard). Clean both at the clipboard
// boundary so none of it ever enters the document.

const NBSP_RE = /\u00A0/g

/** transformPastedHTML: strip filler blocks and normalize nbsp → space. */
export function cleanPastedHTML(html) {
  const doc = new DOMParser().parseFromString(html, 'text/html')

  const walker = doc.createTreeWalker(doc.body, NodeFilter.SHOW_TEXT)
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    n.nodeValue = n.nodeValue.replace(NBSP_RE, ' ')
  }

  // Markdown pipe tables require a header row; pasted HTML tables usually
  // have plain <td> everywhere, which the editor can't serialize (the save
  // guard blocks with table:structure). Promote the first row to header cells.
  for (const tbl of doc.body.querySelectorAll('table')) {
    const firstRow = tbl.querySelector('tr')
    if (!firstRow || [...firstRow.children].some((c) => c.tagName === 'TH')) continue
    for (const td of [...firstRow.children]) {
      if (td.tagName !== 'TD') continue
      const th = doc.createElement('th')
      while (td.firstChild) th.appendChild(td.firstChild)
      td.replaceWith(th)
    }
  }

  // Reverse document order so nested blocks empty out before their parents.
  const blocks = [...doc.body.querySelectorAll('p, div')].reverse()
  const KEEP = 'img, table, hr, ul, ol, blockquote, pre, h1, h2, h3, h4, h5, h6'
  for (const el of blocks) {
    if (el.querySelector(KEEP)) continue
    // nbsp is already normalized away, so trim() catching nothing means the
    // block held only whitespace and/or <br>s — a filler.
    if (!el.textContent.trim()) el.remove()
  }
  return doc.body.innerHTML
}

/** transformPastedText: the plain-text clipboard channel only needs nbsp. */
export function cleanPastedText(text) {
  return text.replace(NBSP_RE, ' ')
}
