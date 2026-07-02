import { isNoCache } from './cacheBust'

const BASE = ''  // same origin via Vite proxy

async function request(method, path, body, isFormData = false, extraHeaders = undefined) {
  const opts = {
    method,
    credentials: 'same-origin',
    headers: {
      ...(isFormData ? {} : { 'Content-Type': 'application/json' }),
      ...(extraHeaders || {}),
    },
    body: body ? (isFormData ? body : JSON.stringify(body)) : undefined,
  }
  let url = BASE + path
  if (isNoCache()) {
    opts.cache = 'no-store'
    if (method === 'GET') {
      url += (url.includes('?') ? '&' : '?') + '_nc=' + Date.now()
    }
  }
  const res = await fetch(url, opts)
  // 401 = session expired/invalid. Tell the app shell to swap in the login
  // screen in place (no page reload — unsaved editor work stays recoverable)
  // and fail this call. Skipped for auth endpoints (401 is a normal outcome
  // there) and public endpoints (they legitimately 401 when the public
  // library is toggled off).
  if (res.status === 401 && !path.startsWith('/api/auth/') && !path.startsWith('/api/public/')) {
    window.dispatchEvent(new CustomEvent('api:unauthorized'))
    throw new Error('Session expired — please log in again')
  }
  if (!res.ok) {
    let msg
    try { msg = (await res.json()).detail } catch { msg = res.statusText }
    const err = new Error((typeof msg === 'string' && msg) || msg?.message || `HTTP ${res.status}`)
    err.status = res.status
    err.detail = msg
    throw err
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
  submitJsonFix: (body)  => post('/api/translate/submit-json-fix', body),
  resolveChapterConflict: (body) => post('/api/translate/resolve-chapter-conflict', body),
  cancelJob:     ()      => post('/api/translate/cancel', {}),
  getJobStatus:  ()      => get('/api/translate/status'),

  // Books
  listBooks:     ()           => get('/api/books'),
  createBook:    (body)       => post('/api/books', body),
  getBook:       (id)         => get(`/api/books/${id}`),
  updateBook:    (id, body)   => put(`/api/books/${id}`, body),
  deleteBook:    (id)         => del(`/api/books/${id}`),
  exportBook:    (id, format) => get(`/api/books/${id}/export?format=${format}`),
  invalidateEpubCache: (id)   => post(`/api/books/${id}/invalidate-epub-cache`, {}),
  uploadCover:   (id, formData) => postForm(`/api/books/${id}/cover`, formData),
  deleteCover:   (id)         => del(`/api/books/${id}/cover`),

  // Search & Replace
  searchBook:      (bookId, body) => post(`/api/books/${bookId}/search`, body),
  replaceInBook:   (bookId, body) => post(`/api/books/${bookId}/replace`, body),
  undoReplace:     (bookId)       => post(`/api/books/${bookId}/undo-replace`, {}),

  // Chapters
  listChapters:        (bookId)       => get(`/api/books/${bookId}/chapters`),
  getChapter:          (bookId, num)  => get(`/api/books/${bookId}/chapters/${num}`),
  getChaptersBatch:    (bookId, nums) => get(`/api/books/${bookId}/chapters/batch?nums=${nums.join(',')}`),
  updateChapter:       (bookId, num, body) => put(`/api/books/${bookId}/chapters/${num}`, body),
  createChapter:       (bookId, body = {}) => post(`/api/books/${bookId}/chapters`, body),

  // Chapter revisions (write editor)
  listRevisions:       (bookId, num)         => get(`/api/books/${bookId}/chapters/${num}/revisions`),
  getRevision:         (bookId, num, revId)  => get(`/api/books/${bookId}/chapters/${num}/revisions/${revId}`),
  restoreRevision:     (bookId, num, revId)  => post(`/api/books/${bookId}/chapters/${num}/revisions/${revId}/restore`, {}),
  renumberChapter:     (bookId, num, newNum) => post(`/api/books/${bookId}/chapters/${num}/renumber`, { new_chapter_number: newNum }),
  deleteChapter:       (bookId, num)  => del(`/api/books/${bookId}/chapters/${num}`),
  setProofread:        (bookId, num, isProofread) => put(`/api/books/${bookId}/chapters/${num}/proofread`, { is_proofread: isProofread }),
  batchDeleteChapters: (bookId, chapters) => post(`/api/books/${bookId}/chapters/batch-delete`, { chapters }),
  batchProofread:      (bookId, chapters, isProofread) => post(`/api/books/${bookId}/chapters/batch-proofread`, { chapters, is_proofread: isProofread }),
  batchRequeue:        (bookId, chapters, retranslationReason = null) => post(`/api/books/${bookId}/chapters/batch-requeue`, { chapters, retranslation_reason: retranslationReason }),
  listChapterGenderedEntities: (bookId, num) => get(`/api/books/${bookId}/chapters/${num}/gendered-entities`),
  pronounRepairChapter: (bookId, num, entityId) => post(`/api/books/${bookId}/chapters/${num}/pronoun-repair`, { entity_id: entityId }),

  // Genres
  listGenres:    ()              => get('/api/books/genres'),

  // Per-book modules
  getModules:    (bookId)       => get(`/api/books/modules${bookId != null ? `?book_id=${bookId}` : ''}`),
  setModuleSettings: (bookId, moduleId, settings) => put(`/api/books/${bookId}/modules/${moduleId}/settings`, { settings }),

  // Prompt templates
  getDefaultPrompt: ()           => get('/api/books/default-prompt'),
  getPrompt:     (bookId)       => get(`/api/books/${bookId}/prompt`),
  setPrompt:     (bookId, body) => put(`/api/books/${bookId}/prompt`, body),
  resetPrompt:   (bookId)       => del(`/api/books/${bookId}/prompt`),

  // Per-book categories
  getBookCategories:   (bookId)       => get(`/api/books/${bookId}/categories`),
  setBookCategories:   (bookId, body) => put(`/api/books/${bookId}/categories`, body),
  resetBookCategories: (bookId)       => del(`/api/books/${bookId}/categories`),
  getCategoryEntityCounts: (bookId)   => get(`/api/books/${bookId}/categories/entity-counts`),

  // All tags used across the library (for autocomplete)
  getAllTags:    ()                   => get('/api/books/tags'),

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
    if (params.origin_chapter != null) q.set('origin_chapter', params.origin_chapter)
    return get(`/api/entities${q.toString() ? '?' + q : ''}`)
  },
  createEntity:     (body)       => post('/api/entities', body),
  updateEntity:     (id, body)   => put(`/api/entities/${id}`, body),
  deleteEntity:     (id)         => del(`/api/entities/${id}`),
  getDuplicates:    (params)     => get('/api/entities/duplicates' + (params ? '?' + new URLSearchParams(params) : '')),
  resolveDuplicate: (body)       => post('/api/entities/resolve-duplicate', body),
  getOriginChapters: (bookId)    => get(`/api/entities/origin-chapters?book_id=${bookId}`),
  getEntityContext: (id)         => get(`/api/entities/${id}/context`),
  getAdvice:        (body)       => post('/api/entities/advice', body),
  propagateChange:  (body)       => post('/api/entities/propagate', body),
  batchEntities:    (body)       => post('/api/entities/batch', body),
  decaseEntity:     (body)       => post('/api/entities/decase', body),

  // Queue
  listQueue:        (bookId)     => get(`/api/queue${bookId != null ? '?book_id=' + bookId : ''}`),
  removeQueueItem:  (id)         => del(`/api/queue/${id}`),
  clearQueue:       (bookId)     => del(`/api/queue${bookId != null ? '?book_id=' + bookId : ''}`),
  addToQueue:       (body)       => post('/api/queue/add', body),
  uploadToQueue:    (formData)   => postForm('/api/queue/upload', formData),
  uploadBatch:      (formData)   => postForm('/api/queue/upload-batch', formData),
  uploadEpub:       (formData)   => postForm('/api/queue/upload-epub', formData),
  uploadFb2:        (formData)   => postForm('/api/queue/upload-fb2', formData),
  processNext:      (body = {})  => post('/api/queue/process-next', body),
  stopAutoProcess:  ()           => post('/api/queue/stop-auto', {}),

  // Site info (public, unauthenticated)
  getSiteInfo:      ()           => get('/api/public/site_info'),

  // Settings
  getSettings:      ()           => get('/api/settings'),
  updateSettings:   (body)       => put('/api/settings', body),
  listProviders:    ()           => get('/api/settings/providers'),
  setApiKey:        (name, body) => post(`/api/settings/providers/${name}/key`, body),
  testProvider:     (name)       => post(`/api/settings/providers/${name}/test`, {}),
  exportDb:         ()           => get('/api/settings/db/export-json'),
  getUnits:         ()           => get('/api/settings/units'),
  updateUnits:      (body)       => put('/api/settings/units', body),

  // Activity log
  getActivityLog:    ()  => get('/api/activity-log'),
  clearActivityLog:  ()  => del('/api/activity-log'),

  // API call logs
  listAllApiCalls: (bookId) => get(`/api/api-calls${bookId != null ? '?book_id=' + bookId : ''}`),
  listApiCalls:    (bookId, chapterNum) => get(`/api/api-calls/${bookId}${chapterNum != null ? '?chapter_number=' + chapterNum : ''}`),
  getApiCall:      (id)       => get(`/api/api-calls/detail/${id}`),
  updateApiCall:   (id, body) => put(`/api/api-calls/detail/${id}`, body),

  // Dictionary
  dictLookup:       (q)          => get(`/api/dict/lookup?q=${encodeURIComponent(q)}`),
  retranslate:      (body)       => post('/api/dict/retranslate', body),

  // WordPress
  wpGetSettings:    ()               => get('/api/wordpress/settings'),
  wpUpdateSettings: (body)           => put('/api/wordpress/settings', body),
  wpTestConnection: ()               => post('/api/wordpress/test', {}),
  wpBookStatus:     (bookId)         => get(`/api/wordpress/books/${bookId}/status`),
  wpPublish:        (bookId, body)   => post(`/api/wordpress/books/${bookId}/publish`, body),
  wpCancelPublish:  (bookId)         => post(`/api/wordpress/books/${bookId}/cancel`, {}),
  wpPublishChapter: (bookId, num, body = {}) => post(`/api/wordpress/books/${bookId}/chapters/${num}/publish`, body),

  // Recommendations
  listRecommendations: (status) => get(`/api/recommendations${status ? '?status=' + status : ''}`),
  countRecommendations: (status) => get(`/api/recommendations/count${status ? '?status=' + status : ''}`),
  updateRecommendation: (id, body) => put(`/api/recommendations/${id}`, body),
  deleteRecommendation: (id) => del(`/api/recommendations/${id}`),

  // Comments (admin moderation)
  listComments:    (params = {}) => {
    const q = new URLSearchParams()
    if (params.status)         q.set('status', params.status)
    if (params.book_id != null)         q.set('book_id', params.book_id)
    if (params.chapter_number != null)  q.set('chapter_number', params.chapter_number)
    if (params.limit != null)  q.set('limit', params.limit)
    if (params.offset != null) q.set('offset', params.offset)
    return get(`/api/comments${q.toString() ? '?' + q : ''}`)
  },
  countCommentsAdmin: (status = 'pending') => get(`/api/comments/count?status=${encodeURIComponent(status)}`),
  getCommentAdmin:    (id)        => get(`/api/comments/${id}`),
  updateCommentAdmin: (id, body)  => put(`/api/comments/${id}`, body),
  deleteCommentAdmin: (id, soft = true) => del(`/api/comments/${id}?soft=${soft ? 'true' : 'false'}`),
  rerunAutomod:       (id)        => post(`/api/comments/${id}/automod-rerun`, {}),

  // Comment bans (uuid/email/ip)
  listCommentBans:  ()      => get('/api/comments/bans/list'),
  createCommentBan: (body)  => post('/api/comments/bans', body),
  removeCommentBan: (id)    => del(`/api/comments/bans/${id}`),

  // Per-book comments toggle
  setBookCommentsEnabled: (bookId, enabled) => put(`/api/comments/book/${bookId}/comments_enabled`, { enabled }),

  // Reader stats
  getReaderStats:   (duration, groupBy = 'ip') =>
    get(`/api/reader-stats?duration=${encodeURIComponent(duration)}&group_by=${encodeURIComponent(groupBy)}`),
  getReaderStatsIpInfo: (ips) => post('/api/reader-stats/ip-info', { ips }),

  // Auth
  authStatus:       ()           => get('/api/auth/status'),
  login:            (body)       => post('/api/auth/login', body),
  logout:           ()           => post('/api/auth/logout', {}),
}

// ------------------------------------------------------------------
// Public (unauthenticated) API — the /api/public/* endpoints used by the
// Library / BookDetail / Reader pages. These bypass the 401 session-expiry
// handling in request() (see above): public endpoints legitimately return
// 401 when the public library is toggled off, and that must not kick the
// SPA to the login screen.
// ------------------------------------------------------------------
export const publicApi = {
  listBooks:        (sort)         => get(`/api/public/books${sort ? `?sort=${encodeURIComponent(sort)}` : ''}`),
  getBook:          (id)           => get(`/api/public/books/${id}`),
  listChapters:     (bookId)       => get(`/api/public/books/${bookId}/chapters`),
  getChapter:       (bookId, num)  => get(`/api/public/books/${bookId}/chapters/${num}`),
  getChaptersBatch: (bookId, nums) => get(`/api/public/books/${bookId}/chapters/batch?nums=${nums.join(',')}`),
  searchBook:       (bookId, body) => post(`/api/public/books/${bookId}/search`, body),

  // Comment count for a chapter. The optional commenter UUID header lets the
  // API include the caller's own pending comments in the count.
  getChapterCommentCount: (bookId, num, commenterUuid) =>
    request('GET', `/api/public/comments/chapter/${bookId}/${num}/count`, undefined, false,
            commenterUuid ? { 'X-Commenter-UUID': commenterUuid } : undefined),
}
