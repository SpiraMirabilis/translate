import { useState, useEffect, useRef, useCallback } from 'react'
import { api } from '../services/api'
import { extractDocBlocks, blockToPm, locateFind } from '../lib/grammarOffsets'
import { grammarPluginKey } from '../lib/grammarExtension'

const IDLE_CHECK_MS = 4000
const INITIAL_CHECK_MS = 1500

/**
 * Orchestrates grammar checking + the LLM polish pass for the write editor.
 * All issue positions live in the ProseMirror plugin state (decorations);
 * this hook only handles network, timers, and popover coordinates.
 */
export function useGrammarCheck(editor, bookId) {
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
  useEffect(() => () => { mountedRef.current = false }, [])

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
  }, [editor])

  const dismiss = useCallback((id) => {
    editor?.commands.dismissGrammarIssue(id)
  }, [editor])

  const closePopover = useCallback(() => {
    editor?.commands.activateGrammarIssue(null)
  }, [editor])

  const addToDictionary = useCallback((word, { global = false } = {}) => {
    // Optimistic: drop every squiggle on this word now; a failed POST just
    // means it re-flags next session.
    editor?.commands.ignoreGrammarWord(word)
    api.addDictionaryWord({ word, book_id: global ? null : parseInt(bookId) }).catch(() => {})
  }, [editor, bookId])

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
      const res = await api.grammarPolish({ text, book_id: parseInt(bookId) })
      if (!mountedRef.current || ed.isDestroyed) return
      // Locate against the CURRENT doc — the user may have kept writing.
      const { blocks, meta } = extractDocBlocks(ed.state.doc)
      const entries = []
      const missed = []
      ;(res.suggestions || []).forEach((s, i) => {
        const posRange = locateFind(blocks, meta, s.find)
        if (!posRange) {
          missed.push(s)
          return
        }
        entries.push({
          ...posRange,
          issue: {
            id: `polish:${i}`,
            source: 'polish',
            kind: 'polish',
            message: s.reason || '',
            shortMessage: 'Polish',
            replacements: [s.replace],
            ruleId: '',
            originalText: s.find,
          },
        })
      })
      ed.commands.setGrammarResults('polish', entries)
      setLeftovers(missed)
      setPolishStats(entries.length || missed.length
        ? { total: entries.length, remaining: entries.length }
        : null)
      if (!entries.length && !missed.length) setPolishError('No suggestions — clean chapter!')
    } catch (e) {
      if (mountedRef.current) setPolishError(e.message || 'Polish failed')
    } finally {
      if (mountedRef.current) setPolishing(false)
    }
  }, [editor, bookId, polishing])

  const clearPolish = useCallback(() => {
    editor?.commands.clearGrammar('polish')
    setPolishStats(null)
    setLeftovers([])
    setPolishError(null)
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
