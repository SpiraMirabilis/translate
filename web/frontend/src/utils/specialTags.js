export const SPECIAL_TAGS = {
  'female protagonist': { symbol: '♀', color: 'pink', label: 'Female MC' },
  'male protagonist':   { symbol: '♂', color: 'blue', label: 'Male MC' },
};

export function getSpecialTag(tags) {
  if (!tags) return null;
  for (const t of tags) {
    if (typeof t !== 'string') continue;
    const s = SPECIAL_TAGS[t.toLowerCase()];
    if (s) return { ...s, raw: t };
  }
  return null;
}

export function isSpecialTag(tag) {
  return typeof tag === 'string' && tag.toLowerCase() in SPECIAL_TAGS;
}
