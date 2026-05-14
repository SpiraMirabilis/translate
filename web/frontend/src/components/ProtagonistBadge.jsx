import { getSpecialTag } from '../utils/specialTags';

const COLOR_CLASSES = {
  pink: {
    light: 'bg-pink-100 text-pink-700 border-pink-300',
    sepia: 'bg-pink-200/70 text-pink-800 border-pink-400/60',
    dark:  'bg-pink-500/20 text-pink-300 border-pink-400/40',
  },
  blue: {
    light: 'bg-blue-100 text-blue-700 border-blue-300',
    sepia: 'bg-blue-200/70 text-blue-800 border-blue-400/60',
    dark:  'bg-blue-500/20 text-blue-300 border-blue-400/40',
  },
};

const OVERLAY_CLASSES = {
  pink: 'bg-pink-600 text-white border-pink-700 shadow-md',
  blue: 'bg-blue-600 text-white border-blue-700 shadow-md',
};

const SIZE_CLASSES = {
  sm: { wrap: 'px-1.5 py-0.5 text-[10px] gap-1',  icon: 10 },
  md: { wrap: 'px-2 py-0.5 text-xs gap-1',         icon: 12 },
  lg: { wrap: 'px-2.5 py-0.5 text-sm gap-1.5',     icon: 14 },
};

function MarsIcon({ size }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="10" cy="14" r="6" />
      <path d="M14 10l6-6" />
      <path d="M15 4h5v5" />
    </svg>
  );
}

function VenusIcon({ size }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="9" r="5.5" />
      <path d="M12 14.5v6" />
      <path d="M9 18h6" />
    </svg>
  );
}

const ICON_BY_COLOR = { pink: VenusIcon, blue: MarsIcon };

export default function ProtagonistBadge({ tags, size = 'md', className = '', showLabel = true, theme = 'dark', overlay = false }) {
  const special = getSpecialTag(tags);
  if (!special) return null;
  const colorCls = overlay
    ? (OVERLAY_CLASSES[special.color] || OVERLAY_CLASSES.blue)
    : ((COLOR_CLASSES[special.color] || COLOR_CLASSES.blue)[theme] || COLOR_CLASSES[special.color].dark);
  const sz = SIZE_CLASSES[size] || SIZE_CLASSES.md;
  const Icon = ICON_BY_COLOR[special.color] || MarsIcon;
  return (
    <span
      title={special.raw}
      className={`inline-flex items-center rounded border font-semibold ${sz.wrap} ${colorCls} ${className}`}
    >
      <Icon size={sz.icon} />
      {showLabel && <span>{special.label}</span>}
    </span>
  );
}
