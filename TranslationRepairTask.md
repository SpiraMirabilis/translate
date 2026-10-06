# Translation Repair Workflow

A guide to reviewing and repairing AI-generated translations of a book stored in the T9 database. The goal is to fix entity translations that are wrong, inconsistent, or miscategorized — and to fix clunky calques — while leaving sound stylistic choices alone.

## When to use this workflow

- Reviewing a translated book chapter-by-chapter for fidelity to source-language fandom terms or canonical translations
- Fixing translator drift (same source word translated differently across chapters)
- Cleaning up mistranslations, clunky calques, formatting issues, and category mismatches
- **Not** for rewriting prose or polishing style — this works at the entity level only

## Priorities (in order)

1. **Incorrect translations** — clear mistranslations, wrong canon terms, wrong kinship/rank/title categories.
2. **Clunky calques** — word-for-word renderings that pile up nouns or read awkwardly. A *primary* target, not a nice-to-have. Flag even single occurrences.
3. **Transliteration → translation** — convert transliterated names to translations where the English is meaningful and not awkward (esp. evocative Daoist/Buddhist place and technique names).

Work in batches of **10 chapters at a time** unless told otherwise.

## With the t9 MCP tools (preferred)

A session started with `claude-review` (repo root; it attaches `mcp-review.json`) has the
**`t9_*` tools**. When they are present, use them instead of the scripts below. They run the
same code (the scripts' logic was extracted into shared functions), but take typed
arguments and return compact text or JSON. The server also **enforces** rules this
document otherwise asks you to remember. When no `t9_*` tools are listed, the scripts
remain the way to work, and everything below still applies as written.

| Step | Tool | Script it replaces |
|---|---|---|
| Book details / notes | `t9_get_book` (categories, gendered categories, progress), `t9_get_book_notes` | `translator.py --list-books`, `get_book_notes.py` |
| Plot summaries | `t9_get_chapter_summaries(chapters=…)` — what happens in a stretch, without reading whole chapters (paginated) | `get_chapter_summaries.py` |
| Batch pull | `t9_list_entities(origin_chapter=…)` (point-in-time notes, `note updated chN` tags, paginated) | `get_entities.py --format text` |
| Context | `t9_entity_context(entities=[…], mentions="1,$", chapters=…)`. Source paragraphs only; read the English with `t9_get_chapter` / `t9_grep_book(field="en")` | `get_entity_context.py` |
| Entity search | `t9_search_entities(pattern, field=…)` | `search_entities.py` |
| Prose search | `t9_grep_book(pattern, field="src"\|"en"\|"both", mode="lines"\|"count"\|"chapters")` | `grep_book.py` |
| Note history | `t9_note_revisions(introduced=…, dropped=…)`, `t9_notes_as_of(chapter)` | `note_revisions.py` (`--as-of` → `t9_notes_as_of`) |
| Origin refresh | `t9_backfill_origin_chapter(mode="recompute")`. The dry run returns each change with its drift `window` `[new, old)` | `backfill_origin_chapter.py --recompute` |
| Fix one / many | `t9_correct_entity`, `t9_bulk_correct_entities(corrections=[…])`, `mode="none"\|"substitute"\|"safer"` | `correct_entity_translation.py`, `bulk_correct_entities.py` |
| Category / delete | `t9_change_entity_category(untranslated=[…])`, `t9_delete_entities(untranslated=[…])` | `(bulk_)change_entity_category.py`, `delete_entity.py` |
| Notes / gender | `t9_set_entity_note`, `t9_set_entity_gender` (recorded as `human` revisions) | ad-hoc `set_entity_note` calls |
| Queue control | `t9_translation_status`, `t9_pause_translation`, `t9_resume_translation` | `translation_status.py`, `stop_auto_process.py`, `start_auto_process.py` |
| Prose replace | `t9_replace_in_chapters`, `t9_undo_replace` | `db.replace_in_chapters(...)` |

What changes when you work through the tools:

- **The idle-queue rule is enforced.** Every entity-DB write (correct, bulk correct, category,
  delete, note, gender, origin apply) is **refused while that book is translating**, and also
  when the admin server can't be reached to check. You don't run `translation_status.py`
  first. You get a refusal naming the chapter in flight, and you answer it with
  `t9_pause_translation`. `force=true` exists for a stale claim only. Prose, footnote and
  reindex tools are never blocked.
- **Pause/resume remembers the run.** `t9_pause_translation(book_id)` stops auto-process for
  that book only and waits for the in-flight chapter to save (it never cancels one). It
  returns the run's models and flags as a `resume_hint`. Pass that straight to
  `t9_resume_translation` afterwards. If it comes back with `needs_human`, the run is
  parked on a GUI decision and the user has to answer it there.
- **Writes default to a dry run** (`dry_run=true` / `apply=false`). Read the counts, then
  call again with `dry_run=false`. The bulk dry run evaluates every entry against the
  *current* text, so counts for cascade entries that overlap overstate what the real run
  will do.
- **Lists go inline.** Corrections, keys and footnote maps are tool arguments, so no
  `/tmp/*.json` files. The one thing still worth keeping is the **origin dry-run
  output**: applying overwrites the `(was N)` values the drift audit needs.
- **Replace undo is per process.** `t9_undo_replace` can only undo a replace made through
  this session's server. The web GUI's undo is separate.
- Chapter specs are comma lists of `N`, `N-M`, `>N`, `<=N`… (`"1-20,45"`), except
  `origin_chapter` filters, which take a single term.

## Core scripts

The CLI route, used when the `t9_*` tools are not attached. All scripts run from the repo
root and take `--book-id <N>` (or `-b <N>`).

### Before you start (book context)

Run these two first, before pulling any entity batch — they orient the whole review:

```
python3 translator.py --list-books | grep -A5 "ID: <N>"   # title, author, language, total chapters
python3 get_book_notes.py <N>                              # book-specific notes (positional arg, no -b)
```

- `--list-books | grep -A5 "ID: <N>"` gives the initial book details: title, author, language, and how many chapters are translated so far (sets the batch frontier).
- `get_book_notes.py <N>` prints the synopsis plus any book-specific conventions the user has recorded (genre quirks, cross-IP fandom terms, naming decisions). **Read these before triaging** — they often pre-decide corrections. Note the positional `<N>` (it does **not** take `-b`).

### Inspection

| Script | Purpose | Key args |
|---|---|---|
| `get_entities.py` | List entities for a book, optionally filtered by chapter range | `-b <id>`, `--origin-chapter <range>`, `--format json\|text` |
| `get_entity_context.py` | Show source-text snippets around entity occurrences | `-b <id>`, `--entities "<t1>,<t2>,..."`, `--mentions "<n1>,...\|$"` |
| `search_entities.py` | Search the entity DB — find translation patterns, collision candidates, propagation targets | `--book <id> [--category C] [--origin-chapter R] [--field {untranslated,translated,both}] [--regex] [--case-sensitive] <pattern>` |
| `grep_book.py` | Search the **chapter text itself** — source and/or translated prose | `-b <id>`, `--field {src,en,both}`, `--chapters <range>`, `-i`, `-F`, `--context N`, `--titles`, `--count`, `-l`, `--no-queue` |
| `backfill_origin_chapter.py` | Re-file `origin_chapter` from the real first appearance in the source. **A write** — needs an idle queue if it moves notes; safe otherwise (see [Refreshing origin_chapter](#refreshing-origin_chapter-every-batch)) | `--book-id <id>`, `--recompute`, `--dry-run` |
| `note_revisions.py` | Read the **entity-note revision log** — when a fact about an entity was first written down, and when it stopped being restated | `-b <id>`, `-e "<entity>"`, `--introduced P`, `--dropped P`, `--grep P`, `--chapters R`, `--author`, `--diff`, `--as-of N` |

- `get_entities.py --origin-chapter` accepts ranges like `1-10`, `25`, or `>40`.
- **Prefer `--format text`** — fewer tokens than JSON.
- `get_entity_context.py --entities` takes a comma-separated list — batch all entities needing context into one call.
- `get_entity_context.py --mentions` picks occurrences (1-indexed, global across chapters; default `"1"`). `$` = last. Examples: `--mentions 2`, `--mentions "1,$"`, `--mentions "2,3,$"`. Adjacent windows in one chapter merge.
- `get_entity_context.py --chapters` **bounds the search to specific chapters** — essential for single-char / very common entities whose global occurrence #1 is a coincidental idiom match (e.g. 蜃 surfacing the ch470 海市蜃楼 idiom instead of the real ch1198 myth-clam). Accepts a comma list `5,7,9`, a range `1-20`, or an open bound `'>20'` / `'<=20'` (quote the `>`/`<` so the shell doesn't redirect). Set it to the entity's `origin_chapter` (from `get_entities.py`) to confirm a short entity is actually used as a name there before any rename/delete.
- `search_entities.py` is the workhorse for **propagation** and **collision detection** (see below). Search `--field translated` for an English substring to find every entity sharing it.
- `grep_book.py` searches **prose**, not the entity DB — the two are complements. Use it to (a) confirm how an entity actually renders on the page before proposing a fix, (b) size a substitution's blast radius and spot Collision-B common words, and (c) verify afterwards that no straggler of the old rendering survives.
  - The pattern is a **regex by default** (`-F` for a literal); alternatives just work: `'killing energy|straw effigy|soul pact'`. There is no `-E` flag — passing one is an error.
  - Each hit is printed as `ch<N> [matched substring] [line index] <whole line>`. **The bracketed match is what makes false positives visible** — a search for `catcher` printing `ch47 [catcher] ... ghost catcher` is how you catch that your footnote candidate has no real anchor. Never act on a count alone.
  - `--field src` finds the source term, `--field en` the English; run both to confirm which source term is behind an English string before any rename (essential for collisions like one English serving two entities).
  - `--no-queue` limits results to translated chapters; without it, untranslated queue rows are included too.

- `note_revisions.py` reads `entity_note_revisions` — the append-only log behind every entity note. Notes are the standing guidance injected into every prompt that mentions the entity, so the log is the only record of *when a fact about a character became true* and of who wrote it (`model` / `human` / `script`).
  - `-e` matches the untranslated text **or** the translation, substring, case-insensitive — one selector finds `许春娘` and `"Xu Chunniang"` alike.
  - **`--introduced PATTERN` is the query you usually want**: it keeps only the revision where the pattern appears in the new note but *not* the previous one — the single row that is the event. A plain `--grep` also matches every later revision that merely carries the clause forward, which on a long-running protagonist is hundreds of rows.
  - `--dropped PATTERN` is the mirror, and is **a lead, not a finding**. Notes cap at 500 chars, so a clause can fall out because it was crowded out by newer facts, not because it stopped being true. Confirm against `grep_book.py` before believing it. Worked example: book 90's protagonist severs her right arm in ch464 (recorded) and regrows it in ch490 (**never recorded**) — the note carried `Severed her own RIGHT ARM in ch464` through 140 further revisions until it silently dropped at ch1330, 840 chapters after it stopped being true.
  - `--as-of N` prints the note as it read at the end of chapter N (the same rewind `get_entities.py` applies automatically for a bounded `--origin-chapter`), and `--diff` shows a word-level diff of each step instead of the full note.
  - ⚠️ **Coverage: this log is only meaningful for books 89-90 and later.** Note revisions became standard with migration 18; a book created before it has no history for notes written earlier, because the pre-migration writes left no creation row. The exception is an older book with a large untranslated backlog at the time — chapters translated after the migration do get logged, so a long-stalled book can carry a partial tail. Measured today: book 90 has 5,605 revisions and book 89 has 206, against 9 for book 15 and 3 for book 20 — nothing at all for every other book. On a pre-89 book, treat an empty result as "no history kept", never as "this fact was never revised".

### Translation corrections

| Script | Use when | Key args |
|---|---|---|
| `correct_entity_translation.py` | Exactly **1** translation to fix | `--book-id`, `--untranslated`, `--translation`, `--substitute` / `--safer-substitute` |
| `bulk_correct_entities.py` | **≥2** translation fixes in one batch | `--book-id`, `--file <json>`, `--substitute` / `--safer-substitute`, `--dry-run` |

The bulk JSON maps `{"<source-term>": "<corrected-translation>"}`. **Python preserves JSON key order**, and bulk processing follows that order — this matters for cascades (see below).

**Substitution flags:**
- `--substitute` — also rewrite existing chapter text (book-wide, case-insensitive, case-*preserving*). Without it, only the entity record changes and chapters keep the old translation.
- `--safer-substitute` — like `--substitute` but restricts the rewrite to chapters whose *source* text actually contains the entity's Chinese. **Use this whenever the old English is a common word or collides with another entity** (see Collisions).
- `--dry-run` — preview counts before applying. Always dry-run a bulk op first.

### Category corrections

| Script | Use when | Key args |
|---|---|---|
| `change_entity_category.py` | Exactly **1** category change | `--book-id`, `--untranslated`, `--new-category` |
| `bulk_change_entity_category.py` | **≥2** category changes | `--book-id`, `--file <json>`, `--dry-run` |

### Deleting entities

Use **`delete_entity.py`** to remove junk / generic / phantom entities — never hand-roll a DB `DELETE`. Dry-run by default; pass `--apply` to commit. Comma-separate the positional arg to delete several at once; `--category C` disambiguates when the same term exists in multiple categories.

```
python3 delete_entity.py -b <id> "辛苦词,另一个"            # dry run (writes nothing)
python3 delete_entity.py -b <id> "辛苦词" --apply           # actually delete
python3 delete_entity.py -b <id> "通用词" --category titles --apply
```

The bulk JSON maps `{"<source-term>": "<new-category>"}`. The new category must exist in the book's template (use `--force` only if you genuinely need a new one). Always `--dry-run` category changes — a wrong category breaks book templates.

## Standard workflow (interactive)

This is the agreed loop. Do **not** batch-apply without surfacing decisions to the user first.

0. **Orient (once per book).** Pull the book details and notes before the first batch. With the tools, that's `t9_get_book` + `t9_get_book_notes`. On the CLI, see [Before you start](#before-you-start-book-context): `translator.py --list-books | grep -A5 "ID: <N>"` and `get_book_notes.py <N>`.

1. **Refresh `origin_chapter` — every batch, not once per book.** Dry-run it, audit the drift windows it reports, *then* apply. This both completes the pull and hands you a worklist of spans that were translated blind. See [Refreshing origin_chapter](#refreshing-origin_chapter-every-batch) — skipping the audit is the most commonly skipped step in this workflow.
   - Tools: `t9_backfill_origin_chapter(book_id, mode="recompute")`. The result lists each change with its `window`. Audit from it, then apply with `apply=true`, which is guarded.
   - CLI:
   ```
   python3 backfill_origin_chapter.py --book-id <id> --recompute --dry-run > /tmp/<book>_backfill_b<n>.txt
   ```

2. **Pull the batch.** Tools: `t9_list_entities(book_id, origin_chapter="<range>")`. CLI:
   ```
   python3 get_entities.py -b <id> --origin-chapter <range> --format text
   ```

3. **Triage** every entity into:
   - **Definitely correct** — canonical/fandom-standard, sensible OCs. Skip.
   - **Definitely wrong** — clear mismatches, format/spelling, internal inconsistency, clunky calques.
   - **Needs context** — ambiguous transliterations, possible canon refs, terms that could map multiple ways.

4. **Get context on BOTH the "needs context" AND the "definitely wrong" buckets** in one batched call (tools: `t9_entity_context(entities=[…])`, up to 50 terms):
   ```
   python3 get_entity_context.py -b <id> --entities "t1,t2,t3,..."
   ```
   Context on the "definitely wrong" set is not optional — it double-checks the diagnosis *and* informs the replacement wording. A confident first impression is still checked against the source.

5. **Present each proposed correction to the user for approval** via the `AskUserQuestion` tool:
   - **Substantive decisions** → one `AskUserQuestion` per correction, offering **1–3 options** with your recommendation first. The tool auto-adds a custom-input choice. Give the source term, current rendering, the reasoning, and the trade-offs in each option's description.
   - **Trivial / standardization fixes** (pure capitalization, applying an already-established naming pattern like "No. X", "X Family", surname-first ordinals, an already-decided convention) → bundle them all into **one yes/no batch** question listing each `源 "old" → "new"`. Don't make the user click through these individually.

6. **Apply** the approved changes (see Applying). With the tools: `t9_pause_translation` → entity writes → `t9_resume_translation(**resume_hint)` → footnotes and prose edits.

7. **Propagate** any approved change whose error pattern recurs in later chapters — *now*, not later. See Propagation.

8. **Move to the next batch.**

## What to flag as wrong

- **Canon / category mismatches** — source maps to a known name but got a literal calque or wrong term; a kinship title mapped to the wrong relation (e.g. 族长 "clan head" rendered "village head"; 姑父 "aunt's husband" given a spurious birth-order number).
- **Rank conflation** — distinct source ranks collapsed to one English word. Keep the system distinct (see Glossary: noble & military ranks).
- **Internal inconsistency** — same source term rendered two ways; or two *different* source terms collapsed to identical English when the author distinguished them (a Collision — see below).
- **Verb/term drift** — the same morpheme rendered inconsistently (e.g. 炼 "refine" sometimes "Refinement", sometimes "tempering" — but 淬 is "tempering"; keep them apart).
- **Format issues** — bracket conventions (`《》` around book titles), capitalization of proper-noun items/places/techniques, "No. X" numbering, spurious "The" on titles, plural/singular drift, surname placement in ordinal names.
- **Religious-register mismatch** — Daoist vs Buddhist vocabulary. `法` is "Dharma" *only* in a genuinely Buddhist context; `传功` is skill/cultivation **transmission/inheritance**, not "Dharma".
- **Clunky calques** — noun-piles and awkward literal strings ("art of reading auspicious vapors", "mountain seal of Five Fingers Mountain", "Household of Prince Dong'an"). Propose better English. Frequency is not the filter.
- **Needless transliteration** — translate evocative names where the English is clean (太素宫 → "Grand Simplicity Palace", 瑶池 → "Jade Pool", 八卦 gates → element names). Keep transliteration when the term is a real place name, an obscure proper noun, or a monk's dharma-name.

## What NOT to flag

- Stylistic-but-correct renderings. A slightly stilted literal that preserves meaning is not a defect.
- OC transliterations that aren't canon-mappable, as long as they're internally consistent.
- Author-supplied bilingual terms — trust the author's intent.
- Cross-cultural references — use the official English of the referenced work (manga, games, JTTW, real history).
- Intentional non-pinyin romanizations (meme names, Cantonese, etc.) — check context before "fixing."

## Collisions (the substitution gotcha)

`--substitute` / `db.replace_in_chapters` is a **case-insensitive, book-wide string replace**. Two failure modes recur constantly — check for both before every substitute:

### A. Two entities share one English string
Substituting one rewrites the other's chapter occurrences too. Examples seen: 古家 & 顾家 both "Gu Family"; 千户大人 & 百户 both "Centurion"; 鲁大师 & 鲁师傅 both "Master Lu"; 图腾钵 & 图腾金钵 both "Totem Bowl"; 百草经注 & 本草经注 both "Annotated Classic of the Hundred Herbs".

Handle by:
- `--safer-substitute` for the entity being changed — restricts to chapters containing *its* Chinese. Works when the two entities live in **different** chapters.
- **DB-only update** (omit `--substitute`) when the colliding entities **co-occur in the same chapter** (e.g. ch116's 千户大人 + 理刑百户). The entity record is corrected; chapter prose keeps the shared English. Note this to the user — a manual paragraph-level fix is the only way to distinguish them in-prose.

### B. The old English is also a common word
Substituting it corrupts unrelated prose. Examples: "chief", "Minister", "Centurion", "green fruit", and the classic adjectives ("Formless", "Gang", "Pearl", "Crane"). Use `--safer-substitute`, or DB-only, or grep the chapter text case-insensitively first and sanity-check the count.

### Cascade ordering
When one entity's English is a substring of another's, **substitute the longer phrase first** so the shorter one doesn't mangle it:
- `定远伯府` ("...'s manor") before `定远伯` ("Earl of Dingyuan")
- `理刑百户` ("...Centurion") before `掌刑千户` ("Chief of Punishment...") when both touch the string "Chief of Punishment"

Order the JSON keys accordingly. A trailing-0 substitution count after a cascade is expected (the longer pass already rewrote it) — the entity record still updates.

## Refreshing `origin_chapter` (every batch)

`origin_chapter` records when an entity was **extracted**, not when it first **appears**.
`backfill_origin_chapter.py --book-id N --recompute` rescans the source and re-files each
entity onto its real first appearance. Run it **once per batch**, not once per book: the
book keeps translating in the background, so each run covers the chapters added since the
last batch and a once-per-book run goes stale immediately.

It earns its place twice, and the second reason is the one that gets forgotten.

### 1. It makes the batch pull complete

An entity whose first mention falls inside your range can be filed under a much later
chapter — or none — and so miss `--origin-chapter` entirely. Treat an un-backfilled range
pull as a floor, never a census.

### 2. Every moved origin is a drift window

**The entity DB only steers chapters translated *after* the record exists.** So when an
origin moves from ch82 back to ch22, that is not merely a filing correction: it says
chapters **22–81 were translated with no glossary entry for that term**. Nothing held its
rendering steady across that span, and nothing will flag it later, because by the time you
review ch82 the record looks like it has always been there.

The window is the half-open span `[new_origin, old_origin)`. The `--recompute` output is
therefore a **worklist**, not a receipt.

**Most windows are clean — that is expected.** The model is usually consistent on its own;
the audit exists for the minority that are not. A run that flags 78 windows and yields 8
real fixes is a normal, useful run.

### How to audit a window

For each moved entity, look in `[new_origin, old_origin)` for chapters where the **source
term appears but the entity's current English does not**, then *read the actual lines*.

⚠️ **Save the dry-run before applying.** Applying overwrites the old values, and the
`(was N)` numbers are the audit's only input. Write it to `/tmp` and audit from the file.

⚠️ **Read the flagged entity's neighbourhood, not just its own lines.** The best find of a
book-98 audit came sideways: chasing one flagged entity revealed that two *different* clans,
郁家 (Yù) and 於家 (Yú), both rendered "the Yu family" — a collision no per-entity check
would have surfaced.

Real drift this has caught: a realm rendered "Qi Refinement **stage**" against an
established "realm"; "formation **lines**" against "formation patterns"; a named text
《Clan History》 appearing as a generic "family history"; and one weapon class rendered
"sword", "**spirit** sword" and "**artifact** sword" before settling on "ritual sword" 100
chapters later.

### The false positives (there are many)

A substring test cannot see grammar, so expect noise and confirm every hit by eye:

- **Morphology** — plurals, attributive hyphenation, verb forms ("meditating" vs "meditation").
- **Short term inside a longer name** — 青烏 occurs only within 青烏弓 "Azure Crow Bow";
  七門 only within 三宗七門 "the Three Sects and Seven Gates".
- **Legitimately both descriptive and proper** — 密林 is "dense woods" early and "the Dense
  Forest" (a place) later. Both are correct.
- **Grammatical role change** — 採氣 as attributive "qi-gathering" rather than "gathering qi".
- **Matches inside an author's note**, which is out of scope for the review entirely.
- **Preposition / word-boundary matches** — 於家 matched 出**於家**族, 用**於家**中, 至**於家**族.
  When most of a short key's hits are like this, the finding is that **the record itself is
  junk** and should be deleted, not that the prose drifted.

⚠️ `--recompute` can also mis-file a prose-looking entity onto a coincidence — it once wanted
to move a stat named 精神 to ch1 on 緊繃的精神 ("tense nerves"). Check any ordinary-word entity
at its proposed new origin before applying, and restore the ones that were already right.

This is the mirror image of [Propagation](#propagation-the-book-is-translated-30-chapters-ahead):
propagation fixes an error *forward* into chapters you have not reviewed yet, while the drift
window checks *backward* into chapters that were translated before the glossary knew the term.

## Propagation (the book is translated ~30 chapters ahead)

The user pre-translates well beyond the review point, so an error you fix now almost certainly recurs in not-yet-reviewed chapters. **When an approved change is the kind that recurs, propagate it immediately** rather than re-discovering it later:

1. After deciding a fix, `search_entities.py --field translated "<old English>"` (and/or `--field untranslated "<morpheme>"`) to find every entity with the same issue across the whole book.
2. Fold the matches into the same batch (confirm with the user — usually a one-line addition to the trivial batch).

Propagation triggers seen this session:
- **Convention changes** — array→Formation, Dharma→Inheritance, 炼→Refinement, a rank mapping. Sweep all entities sharing the old word.
- **Capitalization sweeps** — once "X Family" / "Spirit-X Talisman" / "No. X" is the rule, search the lowercase or verbose variants and fix the lot.
- **Title/name patterns** — surname-first ordinals, "Lord <Rank>", etc.

## Reviewing while the book is still translating

The normal case is that the book is **actively translating while you review it**. Do not
wait for the queue to go idle on its own, and do not skip the batch — take control of the
queue for the few minutes the writes need, then hand it back.

**Only writes to the entity DB need an idle queue. Writes to translated prose do not.**

| Safe while translating | Needs the queue stopped |
|---|---|
| every read (`get_entities.py`, `grep_book.py`, `get_entity_context.py`, `search_entities.py`) | `correct_entity_translation.py` / `bulk_correct_entities.py` — **with or without** `--substitute` |
| `db.replace_in_chapters(...)` and any other direct write to an already-translated chapter | category changes (`change_entity_category.py`, `bulk_change_entity_category.py`) |
| **footnotes** — `add_footnotes.py`, `delete_footnote.py` | `delete_entity.py` |
| book-notes / prompt-template edits | entity-note writes (`set_entity_note`, `substitute_in_entity_notes`) |

### Why the entity DB is the fragile half

A running job takes its own **entity snapshot** (`get_entities_snapshot(book_id)`) and builds
the chapter's prompt from it. Mutate an entity while a chapter is in flight and that chapter is
translated against the *pre-fix* glossary — so it lands carrying exactly the rendering you just
corrected, after your sweep has already run over the chapters that existed. The fix looks
applied, the book quietly disagrees with itself, and the straggler is in a chapter no verification
grep covered because it did not exist yet. The same applies to notes: a note is injected into
every prompt naming its entity, so a stale one re-seeds the old term into the chapter in transit.

Chapter prose is the opposite. The translator only ever **appends** new chapters; it never reads
or rewrites a finished one. Rewriting ch8 while ch55 is being translated touches nothing the job
holds, which is why prose rescues, footnotes and `replace_in_chapters` can all run against a live
queue.

So the per-batch order is: **find the corrections → stop → drain → apply every entity change
(records, categories, deletions, notes, and the substitutions that ride along with them) →
restart → then do the footnotes and any prose edits** while translation is running again.
Footnotes are a large part of a batch and there is no reason to hold the queue for them.

**With the tools** the whole dance is three calls, and the guard makes the unsafe order
impossible rather than merely discouraged:

1. `t9_pause_translation(book_id)`: stops this book's auto-process, waits for the chapter in
   flight to save, and returns `resume_hint`. On `needs_human`, stop and tell the user.
2. Apply every entity change. A write that finds the book running again is refused, so a
   restart in the meantime can't slip a chapter past you.
3. `t9_resume_translation(**resume_hint)`, then the footnotes and prose edits.

**On the CLI:**

⚠️ **Check `translation_status.py` immediately before an entity write, not after.** A queue that
was idle when the batch started may have been restarted while you were reading context.

```bash
python3 stop_auto_process.py -b <N>                # stops this book's run only
until python3 translation_status.py -b <N> --quiet; do sleep 20; done
python3 translation_status.py -b <N>               # confirm: "idle", auto-process off
#   ... apply substitutions here ...
python3 start_auto_process.py -b <N> --no-review --max-chapters 20
```

⚠️ **Confirm the drain from `translation_status.py`, never from an `echo`.** A pipeline's
exit status is the *last* command's, so `stop_auto_process.py ... | tail && echo DRAINED`
prints DRAINED even when the stop failed outright. Substitutions once ran against a live
queue this way. Chain the whole thing with `&&`, and read the status back.

⚠️ **The restart does not remember the run's options.** `start_auto_process.py` must be
given `--no-review`, `--max-chapters`, the model flags and anything else the run had. Read
them off `translation_status.py` *before* stopping — it prints each running book's
`run options` and how many chapters its budget has left (the status payload's `run_options`
/ `auto_remaining`, since 2026-09-29). The MCP server's `t9_pause_translation` captures
them for you and returns a `resume_hint` for `t9_resume_translation`.

`stop_auto_process.py -b <N>` stops only that book's run; without `-b` it stops every
auto-processing run.

**The block is per book.** A sweep on book A is safe while book B translates. If the book
under review is absent from the `jobs` map on `GET /api/translate/status`, it is safe to
mutate regardless of what the aggregate headline says — `translation_status.py`'s
server-wide "BUSY — do not run repair sweeps" banner predates concurrent per-book
translation and is not authoritative on its own.

## Applying

With the tools, steps 1–4 below are `t9_bulk_correct_entities(corrections=[{untranslated,
translation, category?}, …], mode=…)`, first as the default dry run and then with
`dry_run=false`. The list order is the cascade order. Collision-handled entities go in
their own `t9_correct_entity(mode="safer")` or `mode="none"` calls. The same checks apply.

1. Write the correction JSON to **`/tmp`**, named descriptively: `<book>_ch<range>_corrections.json`.
2. **Dry-run** the bulk op and read the per-entity substitution counts.
3. Sanity-check counts against expectations. A surprisingly high count is a red flag for a Collision-B (common word) — investigate before applying.
4. Apply. Collision-handled entities go in separate `correct_entity_translation.py` calls (`--safer-substitute` or DB-only) outside the main bulk file.
5. Report what changed, calling out mistranslations fixed, collisions handled, and anything propagated into future chapters.

## Decision-making tips

- **Verify before correcting.** A memory or first impression ("the canon name is X") is checked against source context before applying.
- **Don't trust prior corrections blindly.** `get_entities.py` may show an `incorrect_translation` field from a superseded value; the current `translation` is not guaranteed right. A past pass can have corrected in the wrong direction.
- **Match the author's distinctions.** If two source terms differ, keep them distinct in English. If they're identical, don't invent a distinction.
- **Watch for author typos.** Visually-similar / homophone characters get swapped in web novels (本草/百草, 冷香茹/冷香菇, 凡级/凡植, 五行决/诀). Decide per case: collapse (treat as typo — usually when both refer to the same in-world entity) or distinguish (intentional). Ask the user when genuinely ambiguous.
- **Read names phonetically** when they look off — Chinese transliterations of foreign names are usually phonetic.
- **Trust the chapter heading.** Chapter titles often spell out the chapter's key entities unambiguously.

## Companion task: Cultural-reference footnotes

Footnoting runs **alongside** this review (same book, same direction of travel) but is a
separate workflow with its own doc: **`IdentifyingFootnotes.md`**. It is additive annotation
only — it never rewrites the translation.

Do not run it from memory or from this file. In particular, footnote candidates come from
**two** sources — the entity pull *and* an LLM scan of the source text (`footnote_scan.py`) —
and the entity pull alone is blind by construction to bare quotations, parodied proverbs and
idioms, which are the highest-value class. `IdentifyingFootnotes.md` carries the density cap,
the triage questions, the anchor pass, and the tool reference.

## File hygiene

- With the tools there are no correction files: lists are passed inline.
- Correction JSONs and scratch artifacts live in `/tmp`, not the project root.
- Name them `<book>_ch<range>_corrections.json` or `<book>_<topic>_correction.json`.
- `--substitute` rewrites chapter rows but does **not** re-render HTML/EPUB exports — re-export downstream if needed.

## Known gotchas (quick list)

- **Substitution is case-insensitive and case-preserving** — a pure-case correction ("azure sword" → "Azure Sword") *does* apply (the new translation is the casing authority), but it also catches every other occurrence of that string. See Collisions.
- **Multiple source variants of one term** (dashes in incantations, character variants, typos) are usually already handled consistently by the translator — verify, don't fix what's right.
- **Bulk category changes** require the target category to exist in the book template.
- **Trailing-0 substitution counts** after a cascade are normal — the longer/earlier pass already did the rewrite.
