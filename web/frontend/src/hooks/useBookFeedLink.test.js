// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useBookFeedLink } from './useBookFeedLink'

const FEED_SELECTOR = 'link[rel="alternate"][type="application/rss+xml"]'
const GLOBAL = '/api/public/feed.rss'

function addHeadLink(href, title) {
  const link = document.createElement('link')
  link.rel = 'alternate'
  link.type = 'application/rss+xml'
  link.title = title
  link.href = href
  document.head.appendChild(link)
  return link
}

const feedLinks = () =>
  [...document.head.querySelectorAll(FEED_SELECTOR)].map(el => el.getAttribute('href'))

afterEach(() => {
  document.head.querySelectorAll(FEED_SELECTOR).forEach(el => el.remove())
})

describe('useBookFeedLink', () => {
  it('swaps the global tag for the book tag, and back on unmount', () => {
    addHeadLink(GLOBAL, 'Recently Translated Chapters')
    const { unmount } = renderHook(() => useBookFeedLink('4', 'Some Book'))
    expect(feedLinks()).toEqual(['/api/public/books/4/feed.rss'])
    expect(document.head.querySelector(FEED_SELECTOR).title).toBe('Some Book — New Chapters')
    unmount()
    expect(feedLinks()).toEqual([GLOBAL])
  })

  it('keeps a server-injected book tag, but still removes it on unmount', () => {
    // Full page load of a book page: server already swapped global → book
    const server = addHeadLink('/api/public/books/4/feed.rss', 'Some Book — New Chapters')
    const { unmount } = renderHook(() => useBookFeedLink('4', 'Some Book'))
    expect(feedLinks()).toEqual(['/api/public/books/4/feed.rss'])
    expect(document.head.contains(server)).toBe(true)
    unmount()
    // baseline restored even though the tag wasn't ours
    expect(feedLinks()).toEqual([GLOBAL])
  })

  it('replaces a stale per-book tag from a previously viewed book', () => {
    addHeadLink('/api/public/books/4/feed.rss', 'Old Book — New Chapters')
    renderHook(() => useBookFeedLink('7', 'New Book'))
    expect(feedLinks()).toEqual(['/api/public/books/7/feed.rss'])
  })

  it('updates the title once book data loads', () => {
    const { rerender } = renderHook(({ title }) => useBookFeedLink('4', title), {
      initialProps: { title: undefined },
    })
    expect(document.head.querySelector(FEED_SELECTOR).title).toBe('New Chapters')
    rerender({ title: 'Some Book' })
    expect(feedLinks()).toEqual(['/api/public/books/4/feed.rss'])
    expect(document.head.querySelector(FEED_SELECTOR).title).toBe('Some Book — New Chapters')
  })

  it('does nothing without a bookId', () => {
    addHeadLink(GLOBAL, 'Recently Translated Chapters')
    renderHook(() => useBookFeedLink(undefined, undefined))
    expect(feedLinks()).toEqual([GLOBAL])
  })

  it('uses the chapter-windowed href when a chapter number is given', () => {
    addHeadLink(GLOBAL, 'Recently Translated Chapters')
    renderHook(() => useBookFeedLink('4', 'Some Book', 120))
    expect(feedLinks()).toEqual(['/api/public/books/4/feed.rss?around=120'])
  })

  it('is a no-op when the server already injected the same windowed tag', () => {
    const server = addHeadLink('/api/public/books/4/feed.rss?around=120', 'Some Book — New Chapters')
    renderHook(() => useBookFeedLink('4', 'Some Book', 120))
    expect(feedLinks()).toEqual(['/api/public/books/4/feed.rss?around=120'])
    expect(document.head.contains(server)).toBe(true)
  })

  it('replaces a stale windowed tag on chapter navigation', () => {
    addHeadLink('/api/public/books/4/feed.rss?around=120', 'Some Book — New Chapters')
    const { rerender } = renderHook(({ n }) => useBookFeedLink('4', 'Some Book', n), {
      initialProps: { n: 120 },
    })
    rerender({ n: 121 })
    expect(feedLinks()).toEqual(['/api/public/books/4/feed.rss?around=121'])
  })

  it('leaves the head alone while the chapter is unresolved (null)', () => {
    const server = addHeadLink('/api/public/books/4/feed.rss?around=120', 'Some Book — New Chapters')
    renderHook(() => useBookFeedLink('4', 'Some Book', null))
    expect(feedLinks()).toEqual(['/api/public/books/4/feed.rss?around=120'])
    expect(document.head.contains(server)).toBe(true)
  })

  it('restores the global tag on unmount even when it was never present', () => {
    // Full page load of a chapter page: server swapped the global tag out,
    // then the user SPA-navigates away — baseline must be recreated.
    addHeadLink('/api/public/books/4/feed.rss?around=120', 'Some Book — New Chapters')
    const { unmount } = renderHook(() => useBookFeedLink('4', 'Some Book', 120))
    unmount()
    expect(feedLinks()).toEqual([GLOBAL])
  })
})
