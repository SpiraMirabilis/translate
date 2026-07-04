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
//   set        { source, entries: [{ from, to, issue }] } — replace that source's decos
//   dismiss    { id }               — remove deco + remember ignore key
//   activate   { id | null }        — open/close the popover
//   ignoreWord { word }             — drop all typo decos on that word (add-to-dictionary)
//   clear      { source? }          — remove all (or one source's) decos
import { Extension } from '@tiptap/core'
import { Plugin, PluginKey, TextSelection } from '@tiptap/pm/state'
import { Decoration, DecorationSet } from '@tiptap/pm/view'

export const grammarPluginKey = new PluginKey('grammarCheck')

const ignoreKey = (issue) => `${issue.ruleId || issue.source}|${issue.originalText}`

const decoClass = (issue, active) =>
  `gram-issue gram-${issue.kind}${active ? ' gram-active' : ''}`

function makeDeco(from, to, issue, active = false) {
  return Decoration.inline(from, to, { class: decoClass(issue, active) }, { issue })
}

const findById = (decos, id) =>
  decos.find(undefined, undefined, (spec) => spec.issue.id === id)[0] || null

/** Rebuild one issue's decoration with/without the active highlight. */
function setActiveClass(decos, doc, id, active) {
  const deco = id ? findById(decos, id) : null
  if (!deco) return decos
  const rebuilt = makeDeco(deco.from, deco.to, deco.spec.issue, active)
  return decos.remove([deco]).add(doc, [rebuilt])
}

function applyMeta(state, action, doc) {
  let { decos, activeId, ignored, ignoredWords } = state
  switch (action.type) {
    case 'set': {
      const stale = decos.find(undefined, undefined, (spec) => spec.issue.source === action.source)
      decos = decos.remove(stale)
      const fresh = (action.entries || [])
        .filter(({ issue }) => !ignored.has(ignoreKey(issue)) &&
          !ignoredWords.has((issue.originalText || '').toLowerCase()))
        .map(({ from, to, issue }) => makeDeco(from, to, issue))
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
      decos = setActiveClass(decos, doc, activeId, false)
      decos = setActiveClass(decos, doc, action.id, true)
      activeId = action.id
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
  return { decos, activeId, ignored, ignoredWords }
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
            const { decos, activeId } = grammarPluginKey.getState(view.state)
            const hit = decos.find(pos, pos)[0]
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
      clearGrammar: (source) => meta({ type: 'clear', source }),

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
          if (replacement) {
            // Preserve marks at the replaced position (bold/italic context).
            const marks = state.doc.nodeAt(from)?.marks ?? state.doc.resolve(from).marks()
            tr.replaceWith(from, to, state.schema.text(replacement, marks))
          } else {
            tr.delete(from, to)
          }
          tr.setMeta(grammarPluginKey, { type: 'dismiss', id })
          dispatch(tr)
        }
        return true
      },

      activateNextGrammarIssue: (dir = 1, source = null) => ({ state, tr, dispatch }) => {
        const { decos, activeId } = grammarPluginKey.getState(state)
        const all = decos
          .find(undefined, undefined, (spec) => !source || spec.issue.source === source)
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
