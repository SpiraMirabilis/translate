import { createContext, useContext } from 'react'
import { Node } from '@tiptap/core'
import { NodeViewWrapper, ReactNodeViewRenderer } from '@tiptap/react'

/**
 * TipTap atom node for in-chapter illustrations (⟦IMG:id⟧ marker lines).
 * Markdown parse/serialize is handled entirely by writeMarkdown.js — this
 * node only carries the marker id and renders the actual image.
 *
 * Image URLs come from the chapter payload's illustrations map (CDN URLs when
 * Spaces is enabled), provided by WriteEditor via context; the admin serve
 * route is the fallback.
 */
export const IllustrationUrlContext = createContext({ urls: {}, bookId: null })

function IllustrationView({ node, selected }) {
  const { urls, bookId } = useContext(IllustrationUrlContext)
  const id = node.attrs.id
  const src = urls?.[id] || (bookId ? `/api/books/${bookId}/illustration/${id}` : null)
  return (
    <NodeViewWrapper
      className={`my-4 flex justify-center rounded ${selected ? 'ring-2 ring-indigo-500' : ''}`}
      data-drag-handle
    >
      {src ? (
        <img
          src={src}
          alt={`Illustration ${id}`}
          className="max-h-[60vh] max-w-full rounded shadow"
          draggable={false}
        />
      ) : (
        <div className="px-4 py-6 text-xs text-slate-500 border border-dashed border-slate-700 rounded">
          Illustration ⟦IMG:{id}⟧
        </div>
      )}
    </NodeViewWrapper>
  )
}

export const Illustration = Node.create({
  name: 'illustration',
  group: 'block',
  atom: true,
  selectable: true,
  draggable: true,

  addAttributes() {
    return { id: { default: null } }
  },

  parseHTML() {
    return [{
      tag: 'div[data-illustration-id]',
      getAttrs: (el) => ({ id: el.getAttribute('data-illustration-id') }),
    }]
  },

  renderHTML({ node }) {
    return ['div', { 'data-illustration-id': node.attrs.id }]
  },

  addNodeView() {
    return ReactNodeViewRenderer(IllustrationView)
  },
})
