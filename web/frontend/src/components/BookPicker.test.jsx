// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, fireEvent, cleanup } from '@testing-library/react'
import BookPicker from './BookPicker'

const books = [
  { id: 35, title: 'The White Snake' },
  { id: 90, title: 'Immortal Path' },
  { id: 9, title: 'Snake Eater' },
]

afterEach(cleanup)

function setup(props = {}) {
  const onChange = vi.fn()
  const utils = render(
    <BookPicker books={books} value="" onChange={onChange}
      extraOptions={[{ value: '', label: 'All books' }]} {...props} />,
  )
  const input = utils.getByRole('combobox')
  return { ...utils, input, onChange }
}

describe('BookPicker', () => {
  it('shows the selected book as "id: title"', () => {
    const { input } = setup({ value: 90 })
    expect(input.value).toBe('90: Immortal Path')
  })

  it('filters by typed text and picks the top match on Enter', () => {
    const { input, onChange, getAllByRole } = setup()
    fireEvent.focus(input)
    fireEvent.change(input, { target: { value: 'snake' } })
    expect(getAllByRole('option').map(o => o.textContent))
      .toEqual(['9: Snake Eater', '35: The White Snake'])
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onChange).toHaveBeenCalledWith('9')
  })

  it('finds a book by id and arrows through matches', () => {
    const { input, onChange } = setup()
    fireEvent.focus(input)
    fireEvent.change(input, { target: { value: '9' } })
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onChange).toHaveBeenCalledWith('90')
  })

  it('keeps extra options selectable', () => {
    const { input, onChange, getByText } = setup({ value: 35 })
    fireEvent.focus(input)
    fireEvent.click(getByText('All books'))
    expect(onChange).toHaveBeenCalledWith('')
  })

  it('Escape reverts the typed text without changing the value', () => {
    const { input, onChange } = setup({ value: 35 })
    fireEvent.focus(input)
    fireEvent.change(input, { target: { value: 'imm' } })
    fireEvent.keyDown(input, { key: 'Escape' })
    expect(onChange).not.toHaveBeenCalled()
    expect(input.value).toBe('35: The White Snake')
  })
})
