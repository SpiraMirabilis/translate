# Footnotes

Footnotes annotate a chapter: an inline `[n]` marker hugging a term, plus a matching
`[n] body` definition line in a block at the bottom of the chapter.

```
The Calabash Brothers[1] charged in.

[1] Calabash Brothers (葫芦兄弟): a 1980s Chinese cartoon.
```

## Why a table

Originally footnotes lived **only** inline in `chapters.translated_content`. A
retranslation (or any bulk re-save) overwrites that column wholesale and destroyed
them. So footnotes are now persisted in a `footnotes` table, and the inline
`[n]` + definition block is a **derived rendering** that is re-applied after every
chapter save. The table is the source of truth; the text in the chapter is a cache.

## Schema (`footnotes`)

Defined in `db_backend.py` (SQLite + MySQL DDL); created automatically on init.

| column         | meaning |
|----------------|---------|
| `id`           | primary key |
| `book_id`      | FK → `books(id)`, `ON DELETE CASCADE` |
| `chapter_id`   | FK → `chapters(id)`, `ON DELETE CASCADE` (stable across renumber/requeue) |
| `anchor`       | the **English** term the marker hugs — the primary re-anchor key |
| `source_term`  | the **Chinese** source term (nullable; backs manual/future source-side re-anchor) |
| `body`         | the definition text, **without** the `[n]` prefix |
| `occurrence`   | which occurrence of `anchor` in the prose to mark (default 1; disambiguates repeated terms) |
| `status`       | `active` or `orphaned` (anchor not found at last render) |
| `is_source`    | `0` = footnote on translated text, `1` = on source text |
| `sort_order`   | manual tiebreak when two footnotes share a position (normally NULL) |
| `created_date`, `modified_date` | timestamps |

Unique key: `(chapter_id, is_source, anchor, occurrence)` — makes inserts idempotent
(re-running a script with an edited body updates instead of duplicating).

The displayed number `[n]` is **not** stored — it is recomputed in reading order on
every render, so numbering never drifts.

## How a save re-applies footnotes

On every `save_chapter`, after the content is written, a hook calls
`rerender_chapter_footnotes(chapter_id)` (skipped when the chapter has no footnote
rows, so initial translation is a no-op). Rendering is done by
`footnotes.render_footnotes(lines, rows)`:

1. strip every existing `[n]` marker and the definition block → clean prose;
2. for each row, find the `occurrence`-th match of its `anchor` in the prose;
   - found → place a marker there;
   - not found → mark the row `orphaned` (its marker is dropped, body not lost);
3. renumber all placed markers `1..N` in reading order and rebuild the definition
   block.

Because this is deterministic and driven entirely by the table, it is idempotent and
converges even on repeated saves. The rendered text is byte-identical to the old
inline format, so the Reader, EPUB export, and `output_formatter.py` need no changes.

When a retranslation changes the wording so the `anchor` no longer appears, the
footnote becomes **orphaned** rather than silently lost, and you re-anchor it by hand.

## Tooling

- **`footnotes.py`** — shared helpers + the `render_footnotes` core renderer.
- **`add_footnotes.py`** — add first-mention footnotes from a `{term: body}` map
  (or `--term/--footnote`). Writes rows, then re-renders.
- **`add_incantation_footnotes.py`** — footnote the first Latin cast of each
  incantation (reads spells from the entity DB).
- **`list_footnotes.py`** — list footnotes; `--orphans` reports orphaned ones,
  `--reanchor <id> --anchor "<new term>"` re-points and re-renders one.
- **`backfill_footnotes.py`** — one-off migration of a book's existing **inline**
  footnotes into the table. Run with `--dry-run` first to review the derived anchors
  and their confidence.

```bash
# Migrate a book's existing inline footnotes into the table
python3 backfill_footnotes.py --book-id 56 --dry-run
python3 backfill_footnotes.py --book-id 56

# Find footnotes that lost their anchor after a retranslation, then fix one
python3 list_footnotes.py --book-id 56 --orphans
python3 list_footnotes.py --book-id 56 --reanchor 123 --anchor "Bullet of Hatred"
```
