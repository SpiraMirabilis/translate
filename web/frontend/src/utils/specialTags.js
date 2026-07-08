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

// "Editor's Choice" is a curation flag surfaced as gilding + a cover badge
// rather than a normal tag chip. Accept common apostrophe/spelling variants.
const EDITORS_CHOICE_TAGS = new Set([
  "editor's choice",
  'editor’s choice',
  'editors choice',
]);

export function isEditorsChoiceTag(tag) {
  return typeof tag === 'string' && EDITORS_CHOICE_TAGS.has(tag.trim().toLowerCase());
}

export function hasEditorsChoice(tags) {
  return Array.isArray(tags) && tags.some(isEditorsChoiceTag);
}

// Special tags are hidden from the normal tag-chip list because they get their
// own visual treatment (protagonist badge, editor's-choice gilding).
export function isSpecialTag(tag) {
  return (typeof tag === 'string' && tag.toLowerCase() in SPECIAL_TAGS) || isEditorsChoiceTag(tag);
}
