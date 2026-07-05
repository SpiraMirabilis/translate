import { useState, useEffect, useRef, useCallback } from 'react'
import { api } from '../services/api'
import { extractDocBlocks, blockToPm, locateFind } from '../lib/grammarOffsets'
import { grammarPluginKey } from '../lib/grammarExtension'

const IDLE_CHECK_MS = 4000
const INITIAL_CHECK_MS = 1500
const POLISH_POLL_MS = 3000
const POLISH_POLL_MAX = 400            // ~20 min safety cap on client polling
const POLISH_RESUME_MAX_AGE_MS = 7 * 24 * 3600 * 1000

const polishDbId = (issueId) => {
  const m = /^polish:(\d+)$/.exec(issueId || '')
  return m ? parseInt(m[1]) : null
}

/**
 * Orchestrates grammar checking + the LLM polish pass for the write editor.
 * All issue positions live in the ProseMirror plugin state (decorations);
 * this hook only handles network, timers, and popover coordinates.
 *
 * Polish runs as a server-side background job (polish_jobs table): the POST
 * returns a job id immediately and we poll. Suggestions persist with per-row
 * status, so `contentReady` re-attaches the latest unresolved job when the
 * editor mounts — navigating away and back never costs a second LLM call.
 */
export function useGrammarCheck(editor, bookId, chapterNum, contentReady) {
  const [enabled, setEnabled] = useState(false)
  const [checking, setChecking] = useState(false)
  const [ltDown, setLtDown] = useState(false)
  const [polishing, setPolishing] = useState(false)
  const [active, setActive] = useState(null)          // { issue, rect }
  const [polishStats, setPolishStats] = useState(null) // { total, remaining }
  const [leftovers, setLeftovers] = useState([])       // unlocatable polish suggestions
  const [polishError, setPolishError] = useState(null)

  const timerRef = useRef(null)
  const inFlightRef = useRef(false)
  const ltDownRef = useRef(false)
  const enabledRef = useRef(false)
  const mountedRef = useRef(true)
  const pollTimerRef = useRef(null)
  const jobIdRef = useRef(null)        // job whose suggestions are on screen
  useEffect(() => () => {
    mountedRef.current = false
    clearTimeout(pollTimerRef.current)
  }, [])

  // Feature discovery — one cheap call per editor mount.
  useEffect(() => {
    let cancelled = false
    api.grammarStatus()
      .then((s) => {
        if (cancelled) return
        setEnabled(!!s.enabled)
        enabledRef.current = !!s.enabled
        setLtDown(s.enabled && !s.languagetool_up)
        ltDownRef.current = s.enabled && !s.languagetool_up
      })
      .catch(() => {})
    return () => { cancelled = true }
  }, [])

  const checkNow = useCallback(async ({ manual = false } = {}) => {
    const ed = editor
    if (!ed || ed.isDestroyed || !enabledRef.current || inFlightRef.current) return
    if (ltDownRef.current && !manual) return // manual click probes recovery
    let extracted
    try {
      extracted = extractDocBlocks(ed.state.doc)
    } catch {
      return // invariant violation — fail silent, tests catch it
    }
    const { blocks, meta } = extracted
    if (!blocks.some((b) => b.trim())) return

    const docAtRequest = ed.state.doc
    inFlightRef.current = true
    setChecking(true)
    try {
      const res = await api.grammarCheck({ blocks, book_id: parseInt(bookId) })
      if (!mountedRef.current || ed.isDestroyed) return
      setLtDown(false)
      ltDownRef.current = false
      if (!ed.state.doc.eq(docAtRequest)) return // stale — idle timer re-arms on next edit
      const entries = []
      for (const m of res.matches || []) {
        const posRange = blockToPm(meta, m.block, m.offset, m.length)
        if (!posRange) continue
        const originalText = (blocks[m.block] || '').slice(m.offset, m.offset + m.length)
        entries.push({
          ...posRange,
          issue: {
            id: `lt:${m.block}:${m.offset}:${m.ruleId}`,
            source: 'lt',
            kind: m.type || 'grammar',
            message: m.message || '',
            shortMessage: m.shortMessage || '',
            replacements: (m.replacements || []).slice(0, 5),
            ruleId: m.ruleId || '',
            originalText,
          },
        })
      }
      ed.commands.setGrammarResults('lt', entries)
    } catch (e) {
      if (e.status === 503) {
        setLtDown(true)
        ltDownRef.current = true
      }
      // Auto-checks fail silent; the toolbar button title reflects ltDown.
    } finally {
      inFlightRef.current = false
      if (mountedRef.current) setChecking(false)
    }
  }, [editor, bookId])
  const checkNowRef = useRef(checkNow)
  useEffect(() => { checkNowRef.current = checkNow }, [checkNow])

  // Idle trigger: re-arm a short timer on every editor update.
  useEffect(() => {
    if (!editor || !enabled) return undefined
    const onUpdate = () => {
      clearTimeout(timerRef.current)
      timerRef.current = setTimeout(() => checkNowRef.current(), IDLE_CHECK_MS)
    }
    editor.on('update', onUpdate)
    // Initial check shortly after mount/enable (content loads run with
    // emitUpdate:false, so there's no update event to piggyback on).
    timerRef.current = setTimeout(() => checkNowRef.current(), INITIAL_CHECK_MS)
    return () => {
      editor.off('update', onUpdate)
      clearTimeout(timerRef.current)
    }
  }, [editor, enabled])

  // Popover state: mirror the plugin's activeId into { issue, rect }.
  useEffect(() => {
    if (!editor || !enabled) return undefined
    const sync = () => {
      const pluginState = grammarPluginKey.getState(editor.state)
      if (!pluginState) return
      const { decos, activeId } = pluginState
      // Track polish counts for the toolbar chip on every transaction.
      const polishDecos = decos.find(undefined, undefined, (s) => s.issue.source === 'polish')
      setPolishStats((prev) => {
        if (!prev && !polishDecos.length) return prev
        if (!polishDecos.length) return prev ? { ...prev, remaining: 0 } : prev
        return { total: prev?.total ?? polishDecos.length, remaining: polishDecos.length }
      })
      if (!activeId) {
        setActive(null)
        return
      }
      const deco = decos.find(undefined, undefined, (s) => s.issue.id === activeId)[0]
      if (!deco) {
        setActive(null)
        return
      }
      const coords = editor.view.coordsAtPos(deco.from)
      setActive({ issue: deco.spec.issue, rect: { left: coords.left, top: coords.bottom } })
    }
    editor.on('transaction', sync)
    return () => editor.off('transaction', sync)
  }, [editor, enabled])

  // Close the popover on scroll (v1 — matches native spellcheckers).
  useEffect(() => {
    if (!active || !editor) return undefined
    const close = () => editor.commands.activateGrammarIssue(null)
    window.addEventListener('scroll', close, true)
    return () => window.removeEventListener('scroll', close, true)
  }, [active, editor])

  const applySuggestion = useCallback((id, replacement) => {
    editor?.chain().focus().applyGrammarSuggestion(id, replacement).run()
    // Persist polish resolutions; failures just mean it re-attaches next visit.
    const dbId = polishDbId(id)
    if (dbId) api.resolvePolishSuggestion(dbId, 'accepted').catch(() => {})
  }, [editor])

  const dismiss = useCallback((id) => {
    editor?.commands.dismissGrammarIssue(id)
    const dbId = polishDbId(id)
    if (dbId) api.resolvePolishSuggestion(dbId, 'dismissed').catch(() => {})
  }, [editor])

  const closePopover = useCallback(() => {
    editor?.commands.activateGrammarIssue(null)
  }, [editor])

  const addToDictionary = useCallback((word, { global = false } = {}) => {
    // Optimistic: drop every squiggle on this word now; a failed POST just
    // means it re-flags next session.
    editor?.commands.ignoreGrammarWord(word)
    api.addDictionaryWord({
      word,
      book_id: global ? null : parseInt(bookId),
      // Record where the word was first added. Meaningless for a global add
      // (no single book), so only send it for a book-scoped entry.
      origin_chapter: global ? null : parseInt(chapterNum),
    }).catch(() => {})
  }, [editor, bookId, chapterNum])

  // Load a job's open suggestions into decorations, locating against the
  // CURRENT doc — the user may have kept writing (or edited elsewhere since;
  // exact find-strings re-anchor across small drift). `fresh` polishes report
  // unlocatable suggestions in the leftovers banner; silent re-attaches drop
  // them — they're stale noise a week later, and they stay 'open' server-side.
  const attachJob = useCallback((job, { fresh } = {}) => {
    const ed = editor
    if (!ed || ed.isDestroyed) return
    jobIdRef.current = job.id
    let blocks, meta
    try {
      ;({ blocks, meta } = extractDocBlocks(ed.state.doc))
    } catch {
      return
    }
    const entries = []
    const missed = []
    for (const s of (job.suggestions || [])) {
      if (s.status !== 'open') continue
      const posRange = locateFind(blocks, meta, s.find)
      if (!posRange) {
        missed.push(s)
        continue
      }
      entries.push({
        ...posRange,
        issue: {
          id: `polish:${s.id}`,
          source: 'polish',
          kind: 'polish',
          message: s.reason || '',
          shortMessage: 'Polish',
          replacements: [s.replace],
          ruleId: '',
          originalText: s.find,
        },
      })
    }
    if (!fresh && !entries.length) return // nothing re-attachable — stay quiet
    ed.commands.setGrammarResults('polish', entries)
    setLeftovers(fresh ? missed : [])
    setPolishStats(entries.length || (fresh && missed.length)
      ? { total: entries.length, remaining: entries.length }
      : null)
    if (fresh && !entries.length && !missed.length) {
      setPolishError((job.suggestions || []).length
        ? 'Suggestions no longer match the text.'
        : 'No suggestions — clean chapter!')
    }
  }, [editor])
  const attachJobRef = useRef(attachJob)
  useEffect(() => { attachJobRef.current = attachJob }, [attachJob])

  const pollJob = useCallback((jobId, { fresh = true, attempt = 0 } = {}) => {
    clearTimeout(pollTimerRef.current)
    api.grammarPolishJob(jobId)
      .then((job) => {
        if (!mountedRef.current) return
        if (job.status === 'running') {
          if (attempt >= POLISH_POLL_MAX) {
            setPolishing(false)
            setPolishError('Polish is taking too long — check back later.')
            return
          }
          pollTimerRef.current = setTimeout(
            () => pollJob(jobId, { fresh, attempt: attempt + 1 }), POLISH_POLL_MS)
          return
        }
        setPolishing(false)
        if (job.status === 'error') {
          setPolishError(job.error || 'Polish failed')
          return
        }
        attachJobRef.current(job, { fresh })
      })
      .catch((e) => {
        if (!mountedRef.current) return
        setPolishing(false)
        setPolishError(e.message || 'Polish failed')
      })
  }, [])

  const runPolish = useCallback(async () => {
    const ed = editor
    if (!ed || ed.isDestroyed || polishing) return
    let extracted
    try {
      extracted = extractDocBlocks(ed.state.doc)
    } catch {
      return
    }
    const text = extracted.blocks.join('\n\n')
    if (!text.trim()) return
    setPolishing(true)
    setPolishError(null)
    setLeftovers([])
    try {
      const res = await api.grammarPolish({
        text,
        book_id: parseInt(bookId),
        chapter_number: parseInt(chapterNum),
      })
      if (!mountedRef.current) return
      jobIdRef.current = res.job_id
      pollJob(res.job_id, { fresh: true })
    } catch (e) {
      if (mountedRef.current) {
        setPolishError(e.message || 'Polish failed')
        setPolishing(false)
      }
    }
  }, [editor, bookId, chapterNum, polishing, pollJob])

  // Re-attach the latest job once the chapter content is in the editor:
  // resume polling if it's still running, or restore its unresolved
  // suggestions (accepted/dismissed rows stay resolved server-side).
  useEffect(() => {
    if (!editor || !enabled || !contentReady || !bookId || !chapterNum) return undefined
    let cancelled = false
    api.grammarPolishLatest(parseInt(bookId), parseInt(chapterNum))
      .then((job) => {
        if (cancelled || !mountedRef.current || !job || !job.id) return
        if (job.status === 'running') {
          jobIdRef.current = job.id
          setPolishing(true)
          pollJob(job.id, { fresh: true })
        } else if (job.status === 'done') {
          const age = Date.now() - (Date.parse(job.created_at) || 0)
          if (age < POLISH_RESUME_MAX_AGE_MS) attachJobRef.current(job, { fresh: false })
        }
      })
      .catch(() => {})
    return () => {
      cancelled = true
      clearTimeout(pollTimerRef.current)
    }
  }, [editor, enabled, contentReady, bookId, chapterNum, pollJob])

  const clearPolish = useCallback(() => {
    editor?.commands.clearGrammar('polish')
    setPolishStats(null)
    setLeftovers([])
    setPolishError(null)
    // Dismiss the remainder server-side so a cleared pass stays cleared.
    if (jobIdRef.current) {
      api.dismissPolishJob(jobIdRef.current).catch(() => {})
      jobIdRef.current = null
    }
  }, [editor])

  const navigatePolish = useCallback((dir) => {
    editor?.commands.activateNextGrammarIssue(dir, 'polish')
  }, [editor])

  // Content-swap invalidation (restore draft/revision, reload from server):
  // exposed so WriteEditor can clear stale decorations after setContent.
  const clearAll = useCallback(() => {
    editor?.commands.clearGrammar()
    setPolishStats(null)
    setLeftovers([])
  }, [editor])

  const clearLeftovers = useCallback(() => setLeftovers([]), [])

  return {
    enabled, checking, ltDown, polishing, active, polishStats, leftovers, polishError,
    checkNow: () => checkNow({ manual: true }),
    runPolish, applySuggestion, dismiss, closePopover, addToDictionary,
    clearPolish, navigatePolish, clearAll, clearLeftovers,
  }
}
