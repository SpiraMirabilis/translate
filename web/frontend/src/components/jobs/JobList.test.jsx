/**
 * @vitest-environment jsdom
 *
 * JobList renders one card per running book. The regression this guards:
 * /api/books/minimal returns `{ books: [...] }`, not a bare array, and reading
 * it as an array threw "(r.data||[]).find is not a function" — which took the
 * whole admin app down via the error boundary, not just the card.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

const listBooksMinimal = vi.fn()
vi.mock('../../services/api', () => ({
  api: { listBooksMinimal: (...a) => listBooksMinimal(...a) },
}))

let jobsValue = null
vi.mock('../../hooks/useJobs', () => ({ useJobs: () => jobsValue }))

const { default: JobList } = await import('./JobList')

function renderList() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}><JobList /></QueryClientProvider>
  )
}

describe('JobList', () => {
  beforeEach(() => {
    // The real endpoint shape.
    listBooksMinimal.mockResolvedValue({ books: [{ id: 14, title: 'Book Fourteen' }] })
    jobsValue = {
      active: [{ book_id: 14, status: 'running', chapter_number: 7, progress: null }],
      maxConcurrent: 3,
    }
  })

  it('renders a card per running book without throwing on the response shape', async () => {
    renderList()
    expect(await screen.findByText('Book Fourteen')).toBeTruthy()
    expect(screen.getByText(/1 of 3 translation slots in use/)).toBeTruthy()
  })

  it('falls back to the book id before titles have loaded', async () => {
    listBooksMinimal.mockResolvedValue({ books: [] })
    renderList()
    expect(await screen.findByText('Book 14')).toBeTruthy()
  })

  it('labels the bookless job', async () => {
    jobsValue = {
      active: [{ book_id: null, status: 'running', chapter_number: null, progress: null }],
      maxConcurrent: 3,
    }
    renderList()
    expect(await screen.findByText('Pasted text')).toBeTruthy()
  })

  it('renders nothing when no job is running', () => {
    jobsValue = { active: [], maxConcurrent: 3 }
    const { container } = renderList()
    expect(container.textContent).toBe('')
  })
})

describe('JobList resilience', () => {
  it('a broken card does not take down the rest of the list', async () => {
    // Mounted on Dashboard, Queue and (via PromptHost) every page, so a render
    // bug here used to mean react-router's full-screen "Unexpected Application
    // Error!" instead of one bad card.
    jobsValue = {
      active: [
        { book_id: 14, status: 'running', chapter_number: 1, progress: null },
        // `progress` shaped wrongly enough to make TranslationProgress throw.
        { book_id: 79, status: 'running', chapter_number: 2,
          get progress() { throw new Error('boom') } },
      ],
      maxConcurrent: 3,
    }
    listBooksMinimal.mockResolvedValue({ books: [{ id: 14, title: 'Book Fourteen' }] })

    renderList()

    // The healthy card still renders...
    expect(await screen.findByText('Book Fourteen')).toBeTruthy()
    // ...and the broken one degrades to an inline notice naming its book.
    expect(screen.getAllByText(/Job card \(book 79\)/).length).toBeGreaterThan(0)
    expect(screen.queryByText(/Job card \(book 14\)/)).toBeNull()
  })
})
