/**
 * @vitest-environment jsdom
 *
 * useUrlState — query-param-backed state. Rendered under a MemoryRouter so
 * useSearchParams/useNavigate have a router context.
 */
import { describe, it, expect } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { useUrlState } from './useUrlState'

const wrapper = (initialEntries) =>
  function Wrapper({ children }) {
    return <MemoryRouter initialEntries={initialEntries}>{children}</MemoryRouter>
  }

describe('useUrlState', () => {
  it('returns the default when the param is absent', () => {
    const { result } = renderHook(() => useUrlState('q', 'dflt'), {
      wrapper: wrapper(['/']),
    })
    expect(result.current[0]).toBe('dflt')
  })

  it('reads an existing param from the URL', () => {
    const { result } = renderHook(() => useUrlState('q', ''), {
      wrapper: wrapper(['/?q=hello']),
    })
    expect(result.current[0]).toBe('hello')
  })

  it('round-trips set → get → delete', () => {
    const { result } = renderHook(() => useUrlState('q', ''), {
      wrapper: wrapper(['/']),
    })
    act(() => result.current[1]('hello'))
    expect(result.current[0]).toBe('hello')

    // Setting the default value removes the param → back to default
    act(() => result.current[1](''))
    expect(result.current[0]).toBe('')

    act(() => result.current[1]('again'))
    expect(result.current[0]).toBe('again')

    // null also removes
    act(() => result.current[1](null))
    expect(result.current[0]).toBe('')
  })

  it('supports functional updates', () => {
    const { result } = renderHook(() => useUrlState('q', ''), {
      wrapper: wrapper(['/?q=ab']),
    })
    act(() => result.current[1]((v) => v + 'c'))
    expect(result.current[0]).toBe('abc')
  })

  it('applies serialize/deserialize', () => {
    const { result } = renderHook(
      () => useUrlState('n', 0, { serialize: String, deserialize: Number }),
      { wrapper: wrapper(['/']) }
    )
    expect(result.current[0]).toBe(0)
    act(() => result.current[1](5))
    expect(result.current[0]).toBe(5)

    // Setting back to the default removes the param
    act(() => result.current[1](0))
    expect(result.current[0]).toBe(0)
  })

  it('keeps independent params from clobbering each other', () => {
    const { result } = renderHook(
      () => {
        const a = useUrlState('a', '')
        const b = useUrlState('b', '')
        return { a, b }
      },
      { wrapper: wrapper(['/?a=1']) }
    )
    act(() => result.current.b[1]('2'))
    expect(result.current.a[0]).toBe('1')
    expect(result.current.b[0]).toBe('2')
  })
})
