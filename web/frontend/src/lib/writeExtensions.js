import { Extension } from '@tiptap/core'
import StarterKit from '@tiptap/starter-kit'
import Typography from '@tiptap/extension-typography'
import { Table, TableRow, TableHeader, TableCell } from '@tiptap/extension-table'
import Code from '@tiptap/extension-code'
import { TextStyle, Color } from '@tiptap/extension-text-style'
import { Illustration } from '../components/write/IllustrationNode'
import { GrammarCheck } from './grammarExtension'

// TipTap's Code mark ships with excludes:"_" (no other marks may coexist),
// which would silently strip bold/underline/color from code spans on load —
// the markdown bridge supports those combinations (**`bold code`**,
// ⟦U⟧`code`⟦/U⟧). Exclude only itself.
const FlexibleCode = Code.extend({ excludes: 'code' })

// Markdown expresses column alignment via the table separator row (:---:),
// which the bridge maps to an `align` attribute on cells. Extend TipTap's
// cells to carry + render it. Merged cells have no markdown form — the
// round-trip guard blocks them, so no merge UI is offered.
const alignAttribute = {
  align: {
    default: null,
    parseHTML: (el) => {
      const m = (el.style?.textAlign || el.getAttribute('align') || '').match(/left|center|right/)
      return m ? m[0] : null
    },
    renderHTML: (attrs) => (attrs.align ? { style: `text-align: ${attrs.align}` } : {}),
  },
}

const AlignedTableHeader = TableHeader.extend({
  addAttributes() {
    return { ...this.parent?.(), ...alignAttribute }
  },
})

const AlignedTableCell = TableCell.extend({
  addAttributes() {
    return { ...this.parent?.(), ...alignAttribute }
  },
})

// XenForo-parity Enter inside table cells: Enter in a cell paragraph inserts
// a hard break (a new line within the cell) instead of splitting the
// paragraph. Walking ancestors from the deepest out means a list INSIDE a
// cell hits listItem first and defers to the list keymap (new list item).
// Shift+Enter (StarterKit HardBreak) is untouched.
const CellEnter = Extension.create({
  name: 'cellEnter',
  priority: 200, // above the core baseKeymap's splitBlock
  addKeyboardShortcuts() {
    return {
      Enter: ({ editor }) => {
        const { $from } = editor.state.selection
        for (let d = $from.depth; d > 0; d -= 1) {
          const name = $from.node(d).type.name
          if (name === 'listItem') return false
          if (name === 'tableCell' || name === 'tableHeader') {
            return editor.commands.setHardBreak()
          }
        }
        return false
      },
    }
  },
})

/**
 * TipTap extensions for the write editor, constrained to exactly the node/
 * mark set writeMarkdown.js can serialize. Anything outside this set would
 * trip the round-trip guard and block saving — keep the two in lockstep.
 */
export function buildWriteExtensions() {
  return [
    StarterKit.configure({
      // Underline is serialized as the ⟦U⟧…⟦/U⟧ inline sentinel (no native
      // markdown form) — StarterKit's default Underline + Ctrl+U just work.
      code: false, // replaced by FlexibleCode below
      link: {
        openOnClick: false,
        autolink: true,
        linkOnPaste: true,
      },
      heading: { levels: [1, 2, 3, 4, 5, 6] },
    }),
    FlexibleCode,
    // Foreground color: textStyle mark with a color attr, serialized as
    // ⟦COLOR:#rrggbb⟧…⟦/COLOR⟧. The bridge canonicalizes to lowercase hex.
    TextStyle,
    Color,
    // Smart quotes, em-dashes, ellipsis as you type — plain Unicode output,
    // matching the typography already present in translated chapters.
    Typography,
    Illustration,
    Table.configure({ resizable: false }), // colwidth has no markdown form
    TableRow,
    AlignedTableHeader,
    AlignedTableCell,
    CellEnter,
    // View-only decorations (grammar squiggles) — no doc/mark changes, so the
    // round-trip guard is unaffected.
    GrammarCheck,
  ]
}
