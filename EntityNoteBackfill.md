# Entity-note backfill — bringing old books' glossaries up to date

**Status:** book 14, Phases 0–2 + multi-stamp (top 30) done (2026-09-24): 225 convention + 96 character notes, 94 milestone stamps, 26
restamps, origin backfilled. Next: fill the multi-stamp gaps (§8), or Phase 3. Worked example: book 14 ch709
(2026-09-21, 30 notes). See §8 for what the runs changed about the plan.
**Problem:** the `note_updates` channel is recent, so every book that was translated before it
carries a glossary whose notes are missing or frozen at whatever chapter last touched them.

---

## 1. What the gap actually looks like

Measured on book 14 (715 chapters, 2,408 entities that appear in at least one indexed chapter):

| appears in | entities | with a note | **without** |
|---|---|---|---|
| 100+ chapters | 26 | 11 | **15** |
| 50–99 | 54 | 13 | **41** |
| 20–49 | 148 | 24 | **124** |
| 10–19 | 180 | 24 | **156** |
| 5–9 | 299 | 30 | **269** |
| 2–4 | 631 | 85 | **546** |
| 1 | 1,070 | 147 | **923** |

So **408 entities appear in ≥10 chapters and 336 of them have no note at all** — including 99 of
130 characters, 61 of 65 places, 34 of 35 organizations. And **255 of those 336 were last seen
more than 50 chapters back**, which is the number that matters most: the `note_updates` channel
only revises entities that appear in chapters being translated *now*, so those 255 will never be
reached by it. They are the permanent hole.

The corollary is the good news: **the live cast self-heals.** Anything still appearing in new
chapters gets maintained by the channel for free. The backfill's job is the quiet ones and the
empty ones, not everything.

---

## 2. Opinion on the proposed rule

> *"Find every important entity (in more than X chapters), and at a minimum write a note for the
> last chapter they were present in."*

**The importance metric is right.** Chapter spread — `COUNT(DISTINCT chapter)` over
`chapter_entities` — is the correct proxy, and it is the one we already learned to trust:
`origin_chapter` says nothing about importance (book 14's "Peanut" looked like a one-scene maid
and turned out to be 179 occurrences across 30 chapters). The table makes the cut concrete:
**X = 10 gives 408 entities, X = 20 gives 228.** I'd start at 20 and drop to 10 on the second pass.

**"Last chapter present" is right for half the glossary and wrong for the other half**, and this
is the one amendment I'd insist on. A note is resolved through `notes_as_of`, which rewinds: a
note stamped at ch700 **does not exist** for any chapter before 700. That is exactly what you
want for a character's current state, and exactly what you don't want for a rendering convention:

| kind of entity | stamp at | why |
|---|---|---|
| **Conventions** — terms, realms, titles, places, organizations, creatures, kingdoms, techniques | **first appearance** | The note is timeless guidance ("妖 = Yao, never demon"; "州 = Prefecture"). Stamped at the last chapter it is invisible for the whole book, so neither the reader's Terms panel nor a retranslation of an early chapter ever sees it — which is most of what the note was for. |
| **Stateful** — characters, and organizations with a fate (destroyed, absorbed) | **last appearance** | Age, realm, rank, allegiance, alive-or-dead. The current state is the useful one, and stamping it late is what keeps it from spoiling earlier chapters. |

Roughly 60% of the ≥10-chapter set is the first kind, so this is not a corner case.

**Two smaller amendments:**

- **Sequence by "will the channel reach it?", not by size.** Do the 255 gone-quiet entities before
  the 81 that are still active, because the active ones are being maintained already.
- **One note is the floor, not the target, for the top ~30 characters.** A single stamp gives a
  flat history: the reader at ch200 and a retranslation of ch200 still get nothing. For the main
  cast, 2–4 stamps at real state changes (a time skip, a death, a promotion, a realm breakthrough)
  are worth the extra reading. Everyone else gets one.

**And a caveat on cost:** a note rides in *every* prompt chunk where its entity appears. A 600-char
note on a term that occurs in 700 chapters is a recurring bill. Budget by importance — one line for
a term, 3–4 for a main character. The ch709 pass averaged ~350 chars.

---

## 3. Invariants — the things that will bite

1. **`db/entities_repo.py::set_entity_note` is the only sanctioned write**, with
   `chapter_number=N`. `bulk_set_entity_note.py` does **not** take a chapter, and a chapter-less
   revision belongs to the present and never rewinds — it would defeat the whole exercise.
2. ⚠️ **Never stamp a note behind a revision that already exists at a later chapter.**
   `notes_as_of` rewinds to the *`previous_note` of the earliest revision after N*, so the new row
   does not even show at N, while the live note silently regresses to the older text. Check
   `SELECT entity_id FROM entity_note_revisions WHERE entity_id IN (…) AND chapter_number > N`
   and skip. Ningxi was skipped for exactly this in the ch709 pass.
3. **Write a given entity's stamps in ascending chapter order**, for the same reason.
4. ⚠️ **Only ≤N facts in a note stamped at N.** A later fact stamped early leaks into the reader's
   Terms panel — and into a retranslation — before the chapter that earns it.
5. **The membership index is substring matching.** A single-char entity (楚) files itself into every
   chapter containing a word that merely contains it (清楚); a morpheme (妖) matches inside its own
   compounds. Check the in-chapter context before claiming the term appears there.
6. **Reindex before counting.** `chapter_entities` is only as current as the glossary was at save
   time, so run `backfill_chapter_entities.py -b N` first or the counts and last-seen chapters lie.
7. **Timing:** entity-DB writes are safe while *other* books translate, but not while this one does.
   Check the per-book `jobs` map on `GET /api/translate/status`, not the headline of
   `translation_status.py`.

---

## 4. The plan

### Phase 0 — make it measurable (build once, run per book)
- `backfill_chapter_entities.py -b N` to true up membership.
- **New: `entity_note_coverage.py -b N`** — the table in §1, plus the gone-quiet count and a
  ranked worklist (`--min-chapters`, `--no-note`, `--last-seen-before`, `--format json`). This is
  what makes the work resumable across sessions and comparable across books; the query is already
  written (scratch copy in this session's notes, ~20 lines).

### Phase 1 — conventions, stamped at first appearance
Categories: places, titles, organizations, kingdoms, cultivation terms, creatures, techniques,
abilities, equipment, books, events, ingredients. ~230 of the ≥10 band in book 14.
Cheap per entity: the note is a definition plus a rendering rule, and the context needed is
`get_entity_context.py -e <term> --mentions '1,$'` — two windows, no chapter reading.

### Phase 2 — the cast, stamped at last appearance
~99 characters in book 14's ≥10 band. Each needs the chapter of its last appearance read (the
translation is enough; the source only where a rendering is in question). The top ~30 get the
multi-stamp treatment from §2.

### Phase 3 — the long tail (2–9 chapters), optional
900 entities in book 14. Only worth it for ones that carry a rendering trap. Otherwise leave them;
an entity in three chapters costs little when it's wrong and a lot to write up.

### Phase 4 — hygiene
Re-run coverage after each phase. Record the resume point **in the book's own memory file**, never
in the memory index — that's the rule the index itself carries.

---

## 5. Don't do this by hand, past the first book

The ch709 pass took a full session for 30 notes, because reading chapters interactively is the
expensive part. The scalable shape is a **drafting script** on a cheap model, reviewed before it
lands — the same shape as the footnote-candidate scanner:

- **`draft_entity_notes.py -b N --min-chapters 20 --category-set conventions`**
  For each entity: assemble untranslated + translation + category + first/last chapter + the
  `get_entity_context.py` windows (and, for characters, the translated text of the last chapter it
  appears in), batch ~10–15 entities per call through the provider system, and demand
  `{untranslated: {note, chapter, kind: convention|stateful, reason}}`.
- **Guards, mirroring `validate_note_updates`:** the key must already be an entity of this book;
  drop empties and no-ops; cap length by kind (≤200 chars convention, ≤500 stateful); refuse any
  entity with a later revision; refuse a note mentioning a chapter number above its stamp.
- **Review before apply** — write the drafts to JSON, eyeball the top of the file, then apply
  through `set_entity_note(..., author='script', chapter_number=…, reason='note backfill')`.
  Every write is a revision, so a bad batch is revertible per row (`note_revisions.py`, and
  `POST /api/entities/note-revisions/{id}/revert`).

Order of magnitude for book 14: 336 noteless ≥10-chapter entities ≈ 25 model calls, versus ~11
sessions at the ch709 rate.

---

## 6. Verification

- `notes_as_of(book, ch)` spot checks at three points per entity class: before the stamp (expect
  the old text or none), at the stamp, after it.
- The reader's **Terms this chapter** panel on an early chapter and a late one — conventions should
  be present in both, character state only from its stamp onward.
- Re-run `entity_note_coverage.py` and diff against the pre-pass table.
- No entity should gain a revision whose `chapter_number` is lower than an existing one.

---

## 7. Rollout

One book at a time, biggest and most-read first. Book 14 is the live example and already has the
ch709 chapter-pass done. Every book created before the channel landed has the same hole; books
still translating will heal their active cast on their own, so the ranking to work down is
**gone-quiet entities × chapter spread**, not book size.

## Appendix — two defects found while writing this, unfixed
- **"Yao Mpress"** — 妖皇 with the E eaten: 11 hits in ch87, 88, 89, 90, 91, 96, 133, 151. Needs a
  case-sensitive scan; MySQL's collation makes `LIKE '%Mpress%'` match "impress" in 181 chapters.
- **God Sovereign gender drift** — ch668 renders 神皇 "He, and He alone"; ch710–711 show a petite
  girl on the throne and use she/her.

---

## 8. Phase 0 result — book 14, 2026-09-23 (after reindex, head ch715)

`python3 entity_note_coverage.py -b 14` judges each entity at its **target chapter** (first
appearance for conventions, last for the book's gendered categories), not by "has a note":

| spread ≥ 10 | entities | ok | todo | blocked | open & gone quiet |
|---|---|---|---|---|---|
| conventions | 282 | 13 | 240 | 29 | 186 |
| stateful (characters) | 130 | 31 | 99 | 0 | 90 |

What this changes:

- **"Noted" overstates coverage for conventions.** 31 conventions that have a note don't have one
  at their first appearance; the note was stamped later. Only 13 of 282 are actually covered.
- ⚠️ **The ch709 pass caused 17 of the 29 `blocked` rows** (宗主, 飞升, 筑基, 仙人境, 四海, 北海 …):
  it stamped conventions at ch709, so their notes exist only from ch709 on, and invariant 2 now
  forbids an early stamp. Phase 1 needs a route for these before it starts. Options: a chapter-less
  rewrite (belongs to the present, never rewinds, so it's visible everywhere), or re-stamping at
  first appearance after removing the ch709 revision. Decide once; it applies to every book whose
  conventions picked up a late note from the `note_updates` channel.
- **34 conventions (≥10 ch) are hidden by `origin_chapter`.** It's later than their first indexed
  appearance, and `notes_as_of` floors on it (魏 origin ch413, first seen ch1). A first-appearance
  stamp stays invisible until origin is backfilled, so run `backfill_origin_chapter.py` before Phase 1.
- **Given name and full name are separate entities** (镜辞/涂山镜辞, 思瑶/秦思瑶, 君梦/归君梦). The
  drafter should pair them rather than write two independent character notes.
- Single-char kingdoms (魏, 燕) carry the `1ch` flag. Their spread is inflated by substring matching.

### Done 2026-09-23: blocked conventions and origin, book 14

- **Origin backfill:** `backfill_origin_chapter.py --recompute` moved 102 origins earlier. The
  dry run proposed 117; 15 were coincidences (天将 in 有一天将我, 神力 in 精神力, 周游列国, 桂花酿,
  …) and are now permanent `backfill_origin_exclusions.json` entries for book 14. Conventions
  hidden by the origin floor went from 34 to 11 (the rest are single-char or excluded keys).
- **Blocked → option B via `restamp_entity_notes.py`** (new, plan-file driven, dry run by default,
  re-checks `notes_as_of` after each write). 26 of 27 restamped: 4 **moves** (note already free of
  later facts; creation row re-stamped at first appearance) and 22 **splits** (a short
  rendering-rule note at first appearance, then the original late text unchanged at its own
  chapter). `entities.note` is untouched. 天将 was left alone: its "first appearance" is the
  coincidence above, so its real first appearance is probably its revision chapter.
- A chapter-less rewrite (option A) would **not** have worked: the late row stays the earliest
  revision after N and still rewinds to its NULL `previous_note`.
- Result, conventions with spread ≥ 10: ok 17 → 43, blocked 29 → 1.
- Caught while splitting: the ch709 notes on 仙人境/筑基 describe the *Deity life's* ladder
  ("above Foundation Establishment", "the first realm"). Stamped early they'd be wrong for the
  earlier lives' different ladder, not just spoilers. **Phase 1 drafting must keep per-life facts
  out of first-appearance notes.**

### Phase 1, book 14, 2026-09-23: the ≥20-chapter band

- `draft_entity_notes.py` (new) drafted on `claude:claude-sonnet-5` at low effort, reviewed by
  hand, and applied through `set_entity_note` at first appearance: **122 notes** (a 12-entity
  trial, then 110). About 1 draft in 6 needed an edit. The recurring faults: tying a generic term
  to one life or storyline, realm-order claims (the per-life ladder problem again), contradicting
  the glossary's capitalization, and invented cross-glossary "patterns". The prompt now forbids
  the last. Conventions with spread ≥ 10: ok 43 → 165, todo 238 → 116.
- Left for a human: 白雪 (first appearance is literal snow), 萧家 (glossary says "Xiao imperial
  family", a per-life reading), 九尾天狐一族 (the glossary has "Heavenly" where 九尾天狐 has
  "Celestial").
- Drift leads found while drafting (the model flagged the chapter's rendering as a mismatch):
  百世书, 北荒, 礼部, 道心, 圣女, 姜仙子. Plus collisions: 齐国/启国 are both "Qi Kingdom",
  梁国/凉国 both "Liang Kingdom".
- Next: the 10–19 band (~113 entities, about 10 calls).
- **10–19 band done the same day: 103 more notes** (105 drafted, 1 redrafted twice and then
  hand-written, 26 hand-edited, 3 skipped). Three rules went into the prompt first: match the
  glossary's capitalization, don't rank realms, don't say whose it is. They removed the ranking
  and capitalization faults, but storyline ties still made up most of the edits. **Phase 1 for
  book 14's ≥10-chapter conventions is complete: ok 268 of 282.** The remainder is 天将, the 3
  skips, and the terms hidden by the origin floor (single-char kingdoms and excluded keys).

### Phase 2, book 14, 2026-09-23: characters, ≥10-chapter band

- `draft_entity_notes.py --kind stateful`: stamped at the **last** appearance, same draft →
  review → apply shape. **96 character notes** written (a 10-name trial plus 8 extras, then 78).
  Characters with spread ≥ 10: ok 31 → 127 of 130. Skipped: 沁阳公主 (the draft muddles Qin
  Siyao with Qin Mujiu), 咪咕 (probably Little Hundun's sound, not a person), 晚风 (nothing
  to say).
- ⚠️ **Context must be chapter summaries, not name-centred prose windows.** The first trial used
  snippets and produced scene trivia with no sense of which life a scene belonged to. Every
  chapter carries a ~420-char `summary`; with the first, the two busiest and the last three
  summaries, the drafts get identity, relationships, life/timeline and state right. Books without
  summaries will need them generated first, or a different context source.
- ⚠️ **Address forms need pointer notes.** 白姑娘, 归姑娘, 严丞相 and others drew full notes that
  contradicted the main character's (Snow White as a "snow-fox"; Gui Junmeng "the same person as"
  Tushan Jingci). Substring pairing doesn't catch them. The prompt now asks for a pointer; 12 were
  converted by hand this time.
- Main-cast notes run long; over-cap drafts are now kept (marked skip, flagged) for a hand trim
  instead of being discarded. Added `--only KEY` for targeted redrafts.
- 36 of 99 characters make their last appearance in the ch640–665 reunion arc (characters from
  the simulated lives meeting for real). Their notes describe that arc's state, which is correct
  but reads differently from a "current state" note.
- **"Bai Ruxue" fixed:** the ch709 pass had used it in five notes (仙人境, 飞升境, 北海, 妖皇, 真龙).
  The glossary rendering is **Snow White** (白如雪, 142 chapters of prose); the pinyin appears in
  one. Replaced via `substitute_in_entity_notes` (chapter-less script revisions, so the early split
  notes are untouched).

### Multi-stamp pass, book 14, 2026-09-24: the top 30 characters

- `draft_note_milestones.py` (new) → `restamp_entity_notes.py` `stamps` plans. **Per character:
  one call picks the turning points from chapter summaries, then one call per stamp that sees only
  summaries up to that stamp** (plus the previous stamp's note), so invariant 4 holds structurally.
  **94 stamps on 30 characters**: 92 on 28 characters, plus 10 on Snow White and Yan Ruxue.
  Snapshots of the prior history are in the session scratchpad.
- Faults found and fixed along the way: notes grew past the cap, because "keep what still holds"
  carries each stamp forward (`--tidy` compresses with a call that sees only the note); picks crowded
  together (stamps <10 chapters apart are collapsed); and picks bunched in the opening chapters. The
  pick prompt now says to cover the span, and **`enforce_coverage` guarantees one stamp per third
  of any span over 300 chapters**, because the model ignored the instruction twice.
- ⚠️ **Snow White / Yan Ruxue had pre-log legacy notes that spoiled the book.** Yan Ruxue's said
  from ch13 that she is Snow White in human form (revealed in-story at ch659); Snow White's said
  "A female dragon" from ch23. `restamp_entity_notes.py` now takes `"replace_legacy": true` (opt-in;
  the discarded text is printed) and `draft_note_milestones.py --replace-legacy`. Other books'
  main casts probably carry the same kind of legacy note. `月神` is book 14's remaining one.
- **Open: gaps in the 28 written before the coverage rule.** The last new stamp sits long before the
  existing late note while the character keeps appearing: Xiao Mo ch280→704 (394 appearances
  in between), Jiang Qingyi ch21→643 (57), Wangxin 260→665 (41), Si Li 269→641 (40), Xuekui
  267→665 (27), Yu Yunwei 269→665 (25). Filling them needs restamp to insert between existing
  stamps (today it only prepends).
- Held for a human: 秦思瑶/秦沐酒 (the drafts consistently treat Princess Qinyang, Qin Mujiu and
  Qin Siyao as one person in the Zhou storyline; if right, the 沁阳公主 skip was wrong and the
  glossary has two entities for one character), and 涂山梦's gender (recorded male; described as
  Abbot Yuankong's wife from ch563).

### Gap fill, book 14, 2026-09-24

- `restamp_entity_notes.py` gained **`insert`**: stamps strictly between two consecutive
  chapter-stamped revisions, with the neighbours relinked so the previous_note chain stays a chain
  (never before the first stamp, never after the last, never across a chapter-less row).
  `draft_note_milestones.py --fill-gaps` finds stretches between stamps where the character keeps
  appearing and drafts inserts there, starting from the note in force at the gap's start.
- **44 inserts on 10 characters**: Xiao Mo 10, Yan Ruxue 5, and 3–4 each for Tushan Jingci, Jiang
  Qingyi, Little Green, Wangxin, Si Li, Wei Xun, Yu Yunwei and Xuekui. Every history was checked
  afterwards: stamped chapters ascending, chain intact.
- ⚠️ **A gap needs a minimum span (`MIN_GAP_SPAN` = 100) as well as appearances.** Without it the
  MC, who is in nearly every chapter, clears the appearance bar in any stretch, so each fill created
  new "gaps" between its own stamps. A second run drafted 27 more for Xiao Mo before this was caught.
  Those were discarded, not applied.
- ⚠️ **The MC's drafts confused the frame story with the avatar lives**: "an avatar life as emperor
  of Zhou", when the Zhou emperor is his real body and the lives run inside the Book of a Hundred
  Lives. All 10 of his notes were rewritten by hand in one fixed shape (frame, court state, current
  life). Any book with a frame story (simulation, reincarnation, system) will need this check.
- **Correction:** 姒璃/司梨 is **not** a collision. 司梨 is the alias under which 姒璃 infiltrates
  the Zhou palace as a maid (a homophone of her own name), so one English name is right.
