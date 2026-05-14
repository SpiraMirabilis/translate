import { isSpecialTag } from '../utils/specialTags';

const SIZE_CLASSES = {
  sm: 'px-1.5 py-0 text-[10px]',
  md: 'px-2 py-0.5 text-xs',
};

const THEME_CLASSES = {
  light: { base: 'bg-stone-300 text-gray-700',     hover: 'hover:bg-stone-400' },
  sepia: { base: 'bg-amber-200/70 text-amber-900', hover: 'hover:bg-amber-300/70' },
  dark:  { base: 'bg-slate-500/20 text-slate-300', hover: 'hover:bg-slate-500/30' },
};

export default function TagChips({
  tags,
  onTagClick,
  size = 'sm',
  className = '',
  includeSpecial = false,
  theme = 'dark',
}) {
  if (!tags || tags.length === 0) return null;
  const visible = includeSpecial ? tags : tags.filter((t) => !isSpecialTag(t));
  if (visible.length === 0) return null;
  const sizeCls = SIZE_CLASSES[size] || SIZE_CLASSES.sm;
  const { base, hover } = THEME_CLASSES[theme] || THEME_CLASSES.dark;
  const clickable = typeof onTagClick === 'function';
  return (
    <span className={`inline-flex flex-wrap items-center gap-1 ${className}`}>
      {visible.map((tag) => {
        const cls = `inline-block rounded font-medium ${sizeCls} ${base} ${
          clickable ? `cursor-pointer ${hover} transition-colors` : ''
        }`;
        return clickable ? (
          <button
            key={tag}
            type="button"
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              onTagClick(tag);
            }}
            className={cls}
          >
            {tag}
          </button>
        ) : (
          <span key={tag} className={cls}>
            {tag}
          </span>
        );
      })}
    </span>
  );
}
