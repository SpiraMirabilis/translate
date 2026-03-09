const BASE = ''  // same origin via Vite proxy

async function request(method, path, body, isFormData = false) {
  const opts = {
    method,
    headers: isFormData ? {} : { 'Content-Type': 'application/json' },
    body: body ? (isFormData ? body : JSON.stringify(body)) : undefined,
  }
  const res = await fetch(BASE + path, opts)
  if (!res.ok) {
    let msg
    try { msg = (await res.json()).detail } catch { msg = res.statusText }
    throw new Error(msg || `HTTP ${res.status}`)
  }
  const ct = res.headers.get('content-type') || ''
  if (ct.includes('application/json')) return res.json()
  return res.blob()
}

const get  = (path)        => request('GET',    path)
const post = (path, body)  => request('POST',   path, body)
const put  = (path, body)  => request('PUT',    path, body)
const del  = (path)        => request('DELETE', path)
const postForm = (path, formData) => request('POST', path, formData, true)

// ------------------------------------------------------------------
// Translation
// ------------------------------------------------------------------
export const api = {
  // Translation
  translate:     (body)  => post('/api/translate', body),
  submitReview:  (body)  => post('/api/translate/submit-review', body),
  skipReview:    ()      => post('/api/translate/skip-review', {}),
  cancelJob:     ()      => post('/api/translate/cancel', {}),
  getJobStatus:  ()      => get('/api/translate/status'),

  // Books
  listBooks:     ()           => get('/api/books'),
  createBook:    (body)       => post('/api/books', body),
  getBook:       (id)         => get(`/api/books/${id}`),
  updateBook:    (id, body)   => put(`/api/books/${id}`, body),
  deleteBook:    (id)         => del(`/api/books/${id}`),
  exportBook:    (id, format) => get(`/api/books/${id}/export?format=${format}`),

  // Chapters
  listChapters:        (bookId)       => get(`/api/books/${bookId}/chapters`),
  getChapter:          (bookId, num)  => get(`/api/books/${bookId}/chapters/${num}`),
  updateChapter:       (bookId, num, body) => put(`/api/books/${bookId}/chapters/${num}`, body),
  deleteChapter:       (bookId, num)  => del(`/api/books/${bookId}/chapters/${num}`),
  setProofread:        (bookId, num, isProofread) => put(`/api/books/${bookId}/chapters/${num}/proofread`, { is_proofread: isProofread }),

  // Prompt templates
  getDefaultPrompt: ()           => get('/api/books/default-prompt'),
  getPrompt:     (bookId)       => get(`/api/books/${bookId}/prompt`),
  setPrompt:     (bookId, body) => put(`/api/books/${bookId}/prompt`, body),
  resetPrompt:   (bookId)       => del(`/api/books/${bookId}/prompt`),

  // Entities
  listEntities:   (params = {}) => {
    const q = new URLSearchParams()
    if (params.book_id === 'global') q.set('global_only', 'true')
    else if (params.book_id != null) {
      q.set('book_id', params.book_id)
      if (params.include_global) q.set('include_global', 'true')
    }
    if (params.category)             q.set('category', params.category)
    if (params.search)               q.set('search',   params.search)
    return get(`/api/entities${q.toString() ? '?' + q : ''}`)
  },
  createEntity:     (body)       => post('/api/entities', body),
  updateEntity:     (id, body)   => put(`/api/entities/${id}`, body),
  deleteEntity:     (id)         => del(`/api/entities/${id}`),
  getDuplicates:    (params)     => get('/api/entities/duplicates' + (params ? '?' + new URLSearchParams(params) : '')),
  resolveDuplicate: (body)       => post('/api/entities/resolve-duplicate', body),
  getAdvice:        (body)       => post('/api/entities/advice', body),
  propagateChange:  (body)       => post('/api/entities/propagate', body),

  // Queue
  listQueue:        (bookId)     => get(`/api/queue${bookId != null ? '?book_id=' + bookId : ''}`),
  removeQueueItem:  (id)         => del(`/api/queue/${id}`),
  clearQueue:       (bookId)     => del(`/api/queue${bookId != null ? '?book_id=' + bookId : ''}`),
  addToQueue:       (body)       => post('/api/queue/add', body),
  uploadToQueue:    (formData)   => postForm('/api/queue/upload', formData),
  uploadBatch:      (formData)   => postForm('/api/queue/upload-batch', formData),
  uploadEpub:       (formData)   => postForm('/api/queue/upload-epub', formData),
  processNext:      (body = {})  => post('/api/queue/process-next', body),
  stopAutoProcess:  ()           => post('/api/queue/stop-auto', {}),

  // Settings
  getSettings:      ()           => get('/api/settings'),
  updateSettings:   (body)       => put('/api/settings', body),
  listProviders:    ()           => get('/api/settings/providers'),
  setApiKey:        (name, body) => post(`/api/settings/providers/${name}/key`, body),
  testProvider:     (name)       => post(`/api/settings/providers/${name}/test`, {}),
  exportDb:         ()           => get('/api/settings/db/export-json'),

  // Activity log
  getActivityLog:    ()  => get('/api/activity-log'),
  clearActivityLog:  ()  => del('/api/activity-log'),

  // Dictionary
  dictLookup:       (q)          => get(`/api/dict/lookup?q=${encodeURIComponent(q)}`),
  retranslate:      (body)       => post('/api/dict/retranslate', body),
}
