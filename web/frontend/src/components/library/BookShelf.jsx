import { ChevronRight } from 'lucide-react'

// A titled horizontal row. Children are fixed-width cards; the row scrolls
// sideways and bleeds to the page edge on phones so the cut-off card hints
// that there is more. The row's pt-1 is the room for the editor's-choice ring
// (2px ring + 2px offset, drawn outside the cover): overflow-x-auto clips the
// y axis too, so without it the top of the gilding is cut off.
export default function BookShelf({ title, icon: Icon, count, action, t, children }) {
  return (
    <section className="mb-10">
      <div className="flex items-baseline justify-between mb-2">
        <h2 className={`flex items-center gap-2 text-base font-semibold ${t.shelfTitle}`}>
          {Icon && <Icon size={16} className="text-indigo-500" />}
          {title}
          {count != null && <span className={`text-xs font-normal ${t.shelfMeta}`}>{count}</span>}
        </h2>
        {action && (
          <button onClick={action.onClick} className={`inline-flex items-center gap-0.5 text-xs ${t.shelfMeta} hover:text-indigo-500 transition-colors`}>
            {action.label} <ChevronRight size={12} />
          </button>
        )}
      </div>
      <div className="-mx-4 px-4 sm:-mx-6 sm:px-6 flex gap-4 overflow-x-auto pt-1 pb-2 snap-x scroll-px-4 sm:scroll-px-6 [scrollbar-width:thin]">
        {children.map ? children.map((c, i) => (
          <div key={c.key ?? i} className="w-[7.5rem] sm:w-36 shrink-0 snap-start">{c}</div>
        )) : children}
      </div>
    </section>
  )
}
