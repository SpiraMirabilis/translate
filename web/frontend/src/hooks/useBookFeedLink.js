import { useEffect } from 'react'

const PER_BOOK_FEED_RE = /^\/api\/public\/books\/\d+\/feed\.rss(\?around=\d+)?$/
// Must mirror the global autodiscovery tag in index.html.
const GLOBAL_FEED_HREF = '/api/public/feed.rss'
const GLOBAL_FEED_TITLE = 'Recently Translated Chapters'

const feedLinks = () =>
  [...document.head.querySelectorAll('link[rel="alternate"][type="application/rss+xml"]')]

function addLink(href, title) {
  const link = document.createElement('link')
  link.rel = 'alternate'
  link.type = 'application/rss+xml'
  link.title = title
  link.href = href
  document.head.appendChild(link)
}

// Book and chapter pages advertise ONLY the current book's RSS feed: the
// site-wide tag from index.html is removed so single-feed autodiscovery
// tools (e.g. Novel Updates) can't pick it by mistake, and restored when
// the page is left. The server does the same swap in the HTML it serves
// for these routes (web/app.py), so on a full page load the effect finds
// the head already in the desired state and changes nothing.
//
// chapterNum semantics: a number → chapter-windowed feed (?around=N →
// chapters N-50..N+100, so feed readers discovering the feed from an older
// chapter URL still see everything from that point on); undefined (arg
// omitted, e.g. BookDetail) → plain book feed; null → "chapter not
// resolved yet" — leave the head alone until the reader knows its chapter.
export function useBookFeedLink(bookId, bookTitle, chapterNum) {
  useEffect(() => {
    if (!bookId || chapterNum === null) return
    const href = `/api/public/books/${bookId}/feed.rss${chapterNum !== undefined ? `?around=${chapterNum}` : ''}`
    for (const el of feedLinks()) {
      const h = el.getAttribute('href') || ''
      // Drop the global tag and per-book tags for other books/chapters
      if (h === GLOBAL_FEED_HREF || (h !== href && PER_BOOK_FEED_RE.test(h))) el.remove()
    }
    if (!feedLinks().some(el => el.getAttribute('href') === href)) {
      addLink(href, bookTitle ? `${bookTitle} — New Chapters` : 'New Chapters')
    }
    return () => {
      // Back to the non-book-page baseline: no per-book tags (including a
      // server-injected one), global tag present.
      for (const el of feedLinks()) {
        if (PER_BOOK_FEED_RE.test(el.getAttribute('href') || '')) el.remove()
      }
      if (!feedLinks().some(el => el.getAttribute('href') === GLOBAL_FEED_HREF)) {
        addLink(GLOBAL_FEED_HREF, GLOBAL_FEED_TITLE)
      }
    }
  }, [bookId, bookTitle, chapterNum])
}
