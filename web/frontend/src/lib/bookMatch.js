/**
 * Filtering/ranking for the BookPicker combobox.
 *
 * A query of digits is usually a book id ("90"), so an exact id wins, then ids
 * that start with it, then titles. Otherwise titles that start with the query
 * rank above ones that merely contain it, and a multi-word query matches when
 * every word appears somewhere in "id: title" ("snake white" still finds
 * "The White Snake"). Ties keep the caller's order (the API sorts by title).
 */
export function bookLabel(b) {
  return `${b.id}: ${b.title ?? ''}`
}

export function rankBooks(books, query) {
  const q = (query || '').trim().toLowerCase()
  if (!q) return books
  const numeric = /^\d+$/.test(q)
  const tokens = q.split(/\s+/)
  const scored = []
  books.forEach((b, i) => {
    const id = String(b.id)
    const title = (b.title || '').toLowerCase()
    let rank
    if (id === q) rank = 0
    else if (numeric && id.startsWith(q)) rank = 1
    else if (title.startsWith(q)) rank = 2
    else if (title.includes(q)) rank = 3
    else if (tokens.every(t => bookLabel({ id, title }).includes(t))) rank = 4
    else return
    scored.push({ rank, i, b })
  })
  scored.sort((a, z) => a.rank - z.rank || a.i - z.i)
  return scored.map(s => s.b)
}
