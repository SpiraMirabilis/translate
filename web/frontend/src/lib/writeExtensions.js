import StarterKit from '@tiptap/starter-kit'
import Typography from '@tiptap/extension-typography'
import { Table, TableRow, TableHeader, TableCell } from '@tiptap/extension-table'
import { Illustration } from '../components/write/IllustrationNode'

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

/**
 * TipTap extensions for the write editor, constrained to exactly the node/
 * mark set writeMarkdown.js can serialize. Anything outside this set would
 * trip the round-trip guard and block saving — keep the two in lockstep.
 */
export function buildWriteExtensions() {
  return [
    StarterKit.configure({
      // Underline has no markdown representation — disabling it also frees
      // Ctrl+U from silently producing an unserializable mark.
      underline: false,
      link: {
        openOnClick: false,
        autolink: true,
        linkOnPaste: true,
      },
      heading: { levels: [1, 2, 3, 4, 5, 6] },
    }),
    // Smart quotes, em-dashes, ellipsis as you type — plain Unicode output,
    // matching the typography already present in translated chapters.
    Typography,
    Illustration,
    Table.configure({ resizable: false }), // colwidth has no markdown form
    TableRow,
    AlignedTableHeader,
    AlignedTableCell,
  ]
}
