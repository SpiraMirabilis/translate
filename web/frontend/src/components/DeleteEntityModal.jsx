/**
 * DeleteEntityModal
 *
 * Replaces the native confirm() for entity deletion.
 * When the translation starts with a capital letter and a book_id is available,
 * offers a third option to delete AND lowercase all occurrences in the book.
 *
 * Props:
 *   entities   - array of { id, translation, book_id } (or single wrapped in array)
 *   onConfirm  - (decase: boolean) => void   called when user confirms
 *   onCancel   - () => void
 */
import { Trash2, CaseLower } from 'lucide-react'

export default function DeleteEntityModal({ entities, onConfirm, onCancel }) {
  const count = entities.length
  const label = count === 1
    ? `"${entities[0].translation}"`
    : `${count} entities`

  // Show decase option if at least one entity has a capitalised translation and a book_id
  const canDecase = entities.some(
    e => e.translation && e.book_id && /^[A-Z]/.test(e.translation)
  )

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60" onClick={onCancel}>
      <div className="bg-slate-800 border border-slate-700 rounded-lg shadow-2xl w-[400px] max-w-[90vw] p-5 space-y-4"
           onClick={e => e.stopPropagation()}>
        <div>
          <h3 className="text-sm font-semibold text-slate-200">Delete {label}?</h3>
          {canDecase && (
            <p className="text-xs text-slate-400 mt-1">
              Some translations are capitalised. You can also lowercase all occurrences in the book.
            </p>
          )}
        </div>

        <div className="flex flex-col gap-2">
          {canDecase && (
            <button
              className="btn-primary flex items-center justify-center gap-2 w-full text-sm"
              onClick={() => onConfirm(true)}
            >
              <CaseLower size={14} />
              Delete &amp; lowercase in book
            </button>
          )}
          <button
            className="btn-secondary flex items-center justify-center gap-2 w-full text-sm"
            onClick={() => onConfirm(false)}
          >
            <Trash2 size={14} />
            Delete{canDecase ? ' only' : ''}
          </button>
          <button
            className="btn-ghost w-full text-sm text-slate-400"
            onClick={onCancel}
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  )
}
