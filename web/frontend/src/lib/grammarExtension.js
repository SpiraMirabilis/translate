// Grammar-check TipTap extension: view-only inline decorations (squiggles)
// carrying issue objects, driven entirely through meta transactions so React
// and the plugin never share mutable state. Decorations can't change the doc,
// so the save round-trip guard is unaffected.
//
// Issue shape (rides in each decoration's spec):
//   { id, source: 'lt'|'polish', kind: 'typo'|'grammar'|'style'|'polish',
//     message, shortMessage, replacements: [string], ruleId, originalText }
//
// Meta actions ({ type, ... } via grammarPluginKey):
//   set            { source, entries: [{ from, to, issue }] } — replace that source's decos
//   dismiss        { id }           — remove deco + remember ignore key
//   activate       { id | null }    — open/close the popover
//   ignoreWord     { word }         — drop all typo decos on that word (add-to-dictionary)
//   ignoreRule     { ruleId }       — drop all decos for that rule (persistence is the hook's job)
//   setHiddenKinds { kinds: Set }   — kind-level view filter: decos stay (counts hold), squiggles hide
//   clear          { source? }      — remove all (or one source's) decos
import { Extension } from '@tiptap/core'
import { Plugin, PluginKey, TextSelection } from '@tiptap/pm/state'
import { Decoration, DecorationSet } from '@tiptap/pm/view'

export const grammarPluginKey = new PluginKey('grammarCheck')

const ignoreKey = (issue) => `${issue.ruleId || issue.source}|${issue.originalText}`

const decoClass = (issue, active, hidden) =>
  `gram-issue gram-${issue.kind}${active ? ' gram-active' : ''}${hidden ? ' gram-hidden' : ''}`

function makeDeco(from, to, issue, active = false, hidden = false) {
  return Decoration.inline(from, to, { class: decoClass(issue, active, hidden) }, { issue })
}

const findById = (decos, id) =>
  decos.find(undefined, undefined, (spec) => spec.issue.id === id)[0] || null

/** Rebuild one issue's decoration with/without the active highlight. */
function setActiveClass(decos, doc, id, active, hiddenKinds) {
  const deco = id ? findById(decos, id) : null
  if (!deco) return decos
  const issue = deco.spec.issue
  const rebuilt = makeDeco(deco.from, deco.to, issue, active, hiddenKinds.has(issue.kind))
  return decos.remove([deco]).add(doc, [rebuilt])
}

function applyMeta(state, action, doc) {
  let { decos, activeId, ignored, ignoredWords, hiddenKinds } = state
  switch (action.type) {
    case 'set': {
      const stale = decos.find(undefined, undefined, (spec) => spec.issue.source === action.source)
      decos = decos.remove(stale)
      const fresh = (action.entries || [])
        .filter(({ issue }) => !ignored.has(ignoreKey(issue)) &&
          !ignoredWords.has((issue.originalText || '').toLowerCase()))
        .map(({ from, to, issue }) => makeDeco(from, to, issue, false, hiddenKinds.has(issue.kind)))
      decos = decos.add(doc, fresh)
      if (activeId && !findById(decos, activeId)) activeId = null
      break
    }
    case 'dismiss': {
      const deco = findById(decos, action.id)
      if (deco) {
        decos = decos.remove([deco])
        ignored = new Set(ignored).add(ignoreKey(deco.spec.issue))
      }
      if (activeId === action.id) activeId = null
      break
    }
    case 'activate': {
      if (activeId === action.id) break
      decos = setActiveClass(decos, doc, activeId, false, hiddenKinds)
      decos = setActiveClass(decos, doc, action.id, true, hiddenKinds)
      activeId = action.id
      break
    }
    case 'ignoreRule': {
      if (!action.ruleId) break
      const gone = decos.find(undefined, undefined, (spec) => spec.issue.ruleId === action.ruleId)
      decos = decos.remove(gone)
      if (activeId && !findById(decos, activeId)) activeId = null
      break
    }
    case 'setHiddenKinds': {
      hiddenKinds = new Set(action.kinds || [])
      // Rebuild every decoration so squiggle visibility matches the new set.
      const all = decos.find()
      decos = DecorationSet.create(doc, all.map((d) => {
        const issue = d.spec.issue
        return makeDeco(d.from, d.to, issue,
          issue.id === activeId, hiddenKinds.has(issue.kind))
      }))
      const activeDeco = activeId ? findById(decos, activeId) : null
      if (activeDeco && hiddenKinds.has(activeDeco.spec.issue.kind)) {
        decos = setActiveClass(decos, doc, activeId, false, hiddenKinds)
        activeId = null
      }
      break
    }
    case 'ignoreWord': {
      const word = (action.word || '').toLowerCase()
      ignoredWords = new Set(ignoredWords).add(word)
      const gone = decos.find(undefined, undefined, (spec) =>
        spec.issue.kind === 'typo' && (spec.issue.originalText || '').toLowerCase() === word)
      decos = decos.remove(gone)
      if (activeId && !findById(decos, activeId)) activeId = null
      break
    }
    case 'clear': {
      const gone = action.source
        ? decos.find(undefined, undefined, (spec) => spec.issue.source === action.source)
        : decos.find()
      decos = decos.remove(gone)
      if (activeId && !findById(decos, activeId)) activeId = null
      break
    }
    default:
      break
  }
  return { decos, activeId, ignored, ignoredWords, hiddenKinds }
}

export const GrammarCheck = Extension.create({
  name: 'grammarCheck',

  addProseMirrorPlugins() {
    return [
      new Plugin({
        key: grammarPluginKey,
        state: {
          init: () => ({
            decos: DecorationSet.empty,
            activeId: null,
            ignored: new Set(),
            ignoredWords: new Set(),
            hiddenKinds: new Set(),
          }),
          apply(tr, value) {
            let next = value
            if (tr.docChanged) {
              next = { ...next, decos: next.decos.map(tr.mapping, tr.doc) }
            }
            const action = tr.getMeta(grammarPluginKey)
            if (action) next = applyMeta(next, action, tr.doc)
            return next
          },
        },
        props: {
          decorations(state) {
            return grammarPluginKey.getState(state).decos
          },
          handleClick(view, pos) {
            const { decos, activeId, hiddenKinds } = grammarPluginKey.getState(view.state)
            const hit = decos.find(pos, pos)
              .filter((d) => !hiddenKinds.has(d.spec.issue.kind))[0]
            const targetId = hit ? hit.spec.issue.id : null
            if (targetId !== activeId) {
              view.dispatch(view.state.tr.setMeta(grammarPluginKey, { type: 'activate', id: targetId }))
            }
            return false // let PM place the cursor as usual
          },
        },
      }),
    ]
  },

  addCommands() {
    const meta = (action) => ({ tr, dispatch }) => {
      if (dispatch) dispatch(tr.setMeta(grammarPluginKey, action))
      return true
    }
    return {
      setGrammarResults: (source, entries) => meta({ type: 'set', source, entries }),
      dismissGrammarIssue: (id) => meta({ type: 'dismiss', id }),
      activateGrammarIssue: (id) => meta({ type: 'activate', id }),
      ignoreGrammarWord: (word) => meta({ type: 'ignoreWord', word }),
      ignoreGrammarRule: (ruleId) => meta({ type: 'ignoreRule', ruleId }),
      setGrammarHiddenKinds: (kinds) => meta({ type: 'setHiddenKinds', kinds }),
      clearGrammar: (source) => meta({ type: 'clear', source }),

      activateGrammarIssueById: (id) => ({ state, tr, dispatch }) => {
        const { decos } = grammarPluginKey.getState(state)
        const deco = findById(decos, id)
        if (!deco) return false
        if (dispatch) {
          tr.setSelection(TextSelection.create(state.doc, deco.from))
          tr.setMeta(grammarPluginKey, { type: 'activate', id })
          tr.scrollIntoView()
          dispatch(tr)
        }
        return true
      },

      applyGrammarSuggestion: (id, replacement) => ({ state, tr, dispatch }) => {
        const { decos } = grammarPluginKey.getState(state)
        const deco = findById(decos, id)
        if (!deco) return false
        const { from, to } = deco
        const issue = deco.spec.issue
        // The user may have edited inside the flagged range since the check.
        if (state.doc.textBetween(from, to, '\n', '\n') !== issue.originalText) {
          if (dispatch) dispatch(tr.setMeta(grammarPluginKey, { type: 'dismiss', id }))
          return false
        }
        if (dispatch) {
          // Replace only the characters that actually changed (common prefix/
          // suffix diff). The check runs on plain text, so a flagged range can
          // span formatting boundaries — rewriting all of it would flatten the
          // marks to those of its first character. Untouched text is never
          // rewritten (keeps its exact marks), and the changed core usually
          // sits inside a single mark run, whose marks it inherits.
          const original = issue.originalText
          const repl = replacement || ''
          const maxShared = Math.min(original.length, repl.length)
          let p = 0
          while (p < maxShared && original[p] === repl[p]) p++
          let s = 0
          while (s < maxShared - p &&
            original[original.length - 1 - s] === repl[repl.length - 1 - s]) s++
          const coreFrom = from + p
          const coreTo = to - s
          const coreText = repl.slice(p, repl.length - s)
          if (coreText) {
            // Pure insertion sits between two runs — take the marks active at
            // the position (the preceding run's), not the following node's.
            const marks = coreFrom < coreTo
              ? state.doc.nodeAt(coreFrom)?.marks ?? state.doc.resolve(coreFrom).marks()
              : state.doc.resolve(coreFrom).marks()
            tr.replaceWith(coreFrom, coreTo, state.schema.text(coreText, marks))
          } else if (coreFrom < coreTo) {
            tr.delete(coreFrom, coreTo)
          }
          tr.setMeta(grammarPluginKey, { type: 'dismiss', id })
          dispatch(tr)
        }
        return true
      },

      activateNextGrammarIssue: (dir = 1, source = null) => ({ state, tr, dispatch }) => {
        const { decos, activeId, hiddenKinds } = grammarPluginKey.getState(state)
        const all = decos
          .find(undefined, undefined, (spec) =>
            (!source || spec.issue.source === source) && !hiddenKinds.has(spec.issue.kind))
          .sort((a, b) => a.from - b.from)
        if (!all.length) return false
        let idx
        const current = activeId ? all.findIndex((d) => d.spec.issue.id === activeId) : -1
        if (current !== -1) {
          idx = (current + dir + all.length) % all.length
        } else {
          const head = state.selection.head
          idx = dir > 0
            ? all.findIndex((d) => d.from > head)
            : all.length - 1 - [...all].reverse().findIndex((d) => d.from < head)
          if (idx === -1 || idx === all.length) idx = dir > 0 ? 0 : all.length - 1
        }
        const target = all[idx]
        if (dispatch) {
          tr.setSelection(TextSelection.create(state.doc, target.from))
          tr.setMeta(grammarPluginKey, { type: 'activate', id: target.spec.issue.id })
          tr.scrollIntoView()
          dispatch(tr)
        }
        return true
      },
    }
  },
})
