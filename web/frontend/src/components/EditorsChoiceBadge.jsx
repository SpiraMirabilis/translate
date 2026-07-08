import { Star } from 'lucide-react';

// Gold pill shown centered at the bottom of a gilded cover to mark an
// editor's-choice pick. Pair with a gold ring on the cover container.
export default function EditorsChoiceBadge({ className = '' }) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border border-amber-300/80 bg-gradient-to-b from-amber-300 to-amber-500 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-amber-950 shadow-md ${className}`}
    >
      <Star size={10} fill="currentColor" strokeWidth={0} />
      Editor's Choice
    </span>
  );
}
