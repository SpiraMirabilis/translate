# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Chinese web novel translation tool that uses AI models (OpenAI/DeepSeek/Claude/Gemini) to translate chapters from Chinese to English. The system includes entity management, queue-based processing, multiple output formats, and book/chapter organization with a modular provider system for supporting multiple AI APIs.

## Architecture

### Core Components
- **translator.py**: Main application entry point and TranslationApp class
- **cli.py**: Command-line interface (CommandLineInterface class) with extensive argparse handling
- **translation_engine.py**: Core translation logic using modular provider system
- **database.py**: Database management with SQLite database (DatabaseManager class)
- **config.py**: Configuration management and provider factory integration (TranslationConfig class)
- **providers/**: Modular AI provider system
  - **base.py**: Abstract base class for all providers (ModelProvider)
  - **factory.py**: Provider factory for creating instances (ProviderFactory)
  - **openai_provider.py**: OpenAI and OpenAI-compatible APIs (OpenAIProvider)
  - **claude_provider.py**: Anthropic Claude API support (ClaudeProvider)
  - **gemini_provider.py**: Google Gemini API support (GeminiProvider)
  - **models.json**: Provider configuration and model definitions
- **logger.py**: Logging functionality
- **ui.py**: Abstract UI base class
- **output_formatter.py**: Handles text/HTML/markdown/EPUB output formatting
- **epub_processor.py**: EPUB file processing for input
- **directory_processor.py**: Batch processing of text files

### Web processes (split 2026-07-10)
The web GUI runs as **two separate uvicorn processes** built from one factory (`web/app_factory.py`, `create_app(public_only=...)`):
- **Admin app** — `web/app.py` → `t9.service` on **127.0.0.1:8000**, behind t9.boondollars.com. Full surface: auth, admin API, WebSocket, translation machinery, plus the public routers (the admin UI's Library/Reader pages use `/api/public/*` too).
- **Public reader app** — `web/public_app.py` → `t9-public.service` on **127.0.0.1:8001**, behind reader.boondollars.com. ONLY `/api/public/*`, `/api/health`, `/simple`, an `/api/auth/status` stub, and the Library/Reader SPA routes (other paths 404). No admin routes/auth/docs exist in the process, so the reader vhost proxies wholesale (`ProxyPass / → :8001`) with **no path whitelist**, and heavy admin/translation work cannot slow public serving.

Cross-process notes:
- Both share MySQL (per-process pools) and `web/frontend/dist`. EPUB/AZW3 builds are already cross-process safe (`ebook_build.py` flock + `.ver` stamps).
- `settings_store` reloads `settings.json` on mtime change, so Settings-UI changes (cache toggles, `public_library`, site names, automod/email settings) reach the public process without a restart. The public app reads the `public_library` gate live per request.
- Comment automod and reply-notification email run inside the public process (BackgroundTasks); they only need `.env`.
- `deploy/t9_watchdog.py` polls both health endpoints, restarts the failing unit (`systemctl --user restart`), and escalates to VM reboot only after 2 restarts don't recover it. Cleanly stopped units are skipped.
- Restart commands: `systemctl --user restart t9.service` (admin), `systemctl --user restart t9-public.service` (public). Restart BOTH after backend code changes that touch shared modules.

### Data Flow
1. Input: clipboard, file, EPUB, or directory of files
2. Text preprocessing and entity extraction
3. Translation via modular provider system (OpenAI/DeepSeek/Claude/Gemini) with entity consistency
4. Output formatting (text/HTML/markdown/EPUB)
5. Storage in SQLite database for book/chapter management

### Provider System Architecture
The application uses a modular provider system for AI models:
- **Provider Factory**: Creates appropriate provider instances based on configuration
- **Provider Interface**: Standardized API for all providers (chat_completion, streaming, etc.)
- **Configuration**: JSON-based provider definitions with support for custom providers
- **Extensibility**: Easy to add new providers by implementing the ModelProvider interface

### Database Schema
Stored in `database.db` SQLite file:
- **entities**: category, untranslated, translation, book_id, etc.
- **books**: title, author, language, creation dates
- **chapters**: book_id, chapter_number, content, metadata

## Common Commands

### Running the Application
```bash
python translator.py [options]
```

### Basic Translation
```bash
# From clipboard
python translator.py --clipboard

# From file
python translator.py --file chapter.txt

# Manual input (prompts for text)
python translator.py
```

### Book Management
```bash
# Create book
python translator.py --create-book "Book Title" --book-author "Author Name"

# List books
python translator.py --list-books

# Export book to EPUB
python translator.py --export-book 1 --format epub
```

### Queue Management
```bash
# Add to queue
python translator.py --file chapter.txt --queue

# Process queue sequentially
python translator.py --resume

# List queue contents
python translator.py --list-queue
```

### Model Configuration
```bash
# List available providers and models
python translator.py --list-providers

# Use DeepSeek
python translator.py --model deepseek:deepseek-chat --file chapter.txt

# Use OpenAI GPT-4
python translator.py --model oai:gpt-4-turbo --file chapter.txt

# Use Claude
python translator.py --model claude:claude-3-5-sonnet-20241022 --file chapter.txt

# Use Gemini
python translator.py --model gemini:gemini-2.5-flash-preview-05-20 --file chapter.txt

# Specify API key directly
python translator.py --model claude:claude-3-5-sonnet --key sk-ant-api-key --file chapter.txt
```

## Key Features

### Database Management
- **Entity Management**: Tracks characters, places, organizations, abilities, titles, equipment
- **Book Management**: Create, update, delete books with metadata
- **Chapter Management**: Store and retrieve chapter content and translations
- Maintains translation consistency across chapters
- Interactive duplicate resolution
- SQLite storage with migration from legacy JSON

### Multi-format Output
- Text, HTML, Markdown, EPUB formats
- Book-level EPUB generation combining multiple chapters
- Configurable output formatting

### Search & Replace
- **Chapter-level**: Instant client-side search with match highlighting in both source and translated panes
- **Book-wide**: API-powered search across all chapters with cross-chapter navigation
- **Scopes**: Search translated text, source text, or both
- **Regex support**: Toggle regex mode for pattern-based search
- **Replace**: Single match or Replace All (translated text only — source is read-only)
- **Chapter titles are included.** `replace_in_chapters(..., include_titles=True)` rewrites `chapters.title` as well as `translated_content`, because the title is a separate column that terminology sweeps used to miss (book 69 kept "Pictographic Fist" in the ch74 title and "Cotton-Cloth Town" in the ch142 title long after the prose was fixed). Pass `include_titles=False` for prose only; the API request accepts the same field. The result dict reports `title_replacements` separately, and a chapter whose *title alone* matches still counts as affected.
- ⚠️ Plain (non-regex) matching is **case-insensitive**, so `"Terracotta warrior"` also matches the lowercase form and any plural containing it. Where casing carries meaning, pass a regex or do a case-sensitive pass of your own.
- **Undo**: Book-wide Replace All stores a snapshot of content *and* title; one-level undo per book (in-memory, class-level `DatabaseManager._replace_undo` dict)
- **Global search modal**: Available from the Books page (`Ctrl+F`), searches across chapters and navigates into the Chapter Editor with search pre-loaded
- **API endpoints**: `POST /api/books/{id}/search`, `POST /api/books/{id}/replace`, `POST /api/books/{id}/undo-replace`
- **Frontend**: `useSearch` hook (`web/frontend/src/hooks/useSearch.js`), `SearchBar` component, `GlobalSearchModal` component

### Original works & the Write editor
The system also hosts **original fiction** written directly in the browser, alongside translations:
- **`books.is_original`** flag (migration 10) — set on the book create/edit form ("Original work" checkbox). Original books skip genre presets, store `source_language='en'`, and have empty `untranslated_content`.
- **WriteEditor** (`web/frontend/src/pages/WriteEditor.jsx`, route `/books/:id/chapters/:n/write`) — TipTap v3 WYSIWYG editor. The Edit button routes by `is_original`; `/edit` deep links redirect to `/write` for original books, so `/edit` stays the universal entry. **Translation books can opt in too** (secondary pen icon on the Books chapter row, or the URL directly) — their default stays the split-pane ChapterEditor, and the write editor shows a Languages icon linking back to `/edit`.
- **Storage stays Markdown line arrays** — `web/frontend/src/lib/writeMarkdown.js` converts lines ⇄ TipTap JSON. Parsing reuses chapterMarkdown's markdown-it instance (`parseMarkdownTokens`); serialization is hand-rolled with minimal escaping. **Every save runs `roundTrip()`** (serialize → reparse → canonical compare) and blocks on mismatch, so the editor can never silently rewrite content. Reader/EPUB/WordPress are untouched.
- **Tables are supported** in two storage forms. *Simple* tables (every cell = one paragraph, no breaks) stay GFM pipe tables — serializer reproduces both corpus styles byte-for-byte (`| --- |` padded for unaligned, `|:---|` compact for left-aligned chatgroup). *Rich* tables (XenForo-parity: multi-line cells, lists, blockquotes, multiple paragraphs) use whole-line sentinel markers with explicit terminators: `⟦TABLE⟧ ⟦TR⟧ ⟦TH:align⟧…⟦/TH⟧ ⟦TD⟧…⟦/TD⟧ ⟦/TR⟧ ⟦/TABLE⟧`, cell interiors = ordinary markdown lines. The serializer picks the form per table (`isSimpleCell` in writeMarkdown.js); grammar lives in chapterMarkdown.js (`parseTableRun`/`renderTable`) and is mirrored in output_formatter.py (`_parse_table_run`/`_render_table_html`, used by Reader parity, EPUB, HTML export, and WordPress via `render_lines_html`). bbcode.js maps sentinel tables 1:1 onto `[TABLE][TR][TD]` with block content in cells. In the editor, **Enter inside a cell inserts a hard break** (CellEnter extension; Enter in a list-in-cell still splits the item). Still blocked by the round-trip guard: merged cells, header cells outside row 0, headings/code/hr/nested tables/illustrations inside cells, literal `⟦…⟧` marker text. Malformed sentinel runs downgrade to literal text and open the editor read-only (`table:malformed`). Toolbar: insert-table + row/col controls when inside a table.
- **Empty-chapter creation**: `POST /api/books/{id}/chapters` `{title?, chapter_number?}` (defaults to max+1) — the "New Chapter" button on original books.
- **Server autosave**: 30s idle / 90s max while dirty; optimistic lock via `expected_translation_date` (409 on mismatch → conflict banner with reload/overwrite).
- **Revision snapshots**: `chapter_revisions` table (JSON line-array content, `kind` manual/auto, pruned to newest 50 manual + 20 auto per chapter; `db/revisions_repo.py`). Explicit save (Ctrl+S) records a `manual` revision; autosaves coalesce to one `auto` per 10 min. Endpoints under `/api/books/{id}/chapters/{n}/revisions` (`web/api/revisions.py`); restore snapshots current content first. History slide-over in the editor toolbar.
- **Writing features**: live word count, session counter, daily goal (localStorage), focus/typewriter mode (Ctrl+Shift+F, Esc exits), Reader-parity preview toggle, illustrations rendered inline as atom nodes (⟦IMG:id⟧ markers).
- **Underline & text color** (XenForo parity — no markdown form): inline `⟦⟧` sentinels in storage — `⟦U⟧…⟦/U⟧` and `⟦COLOR:#rrggbb⟧…⟦/COLOR⟧` (lowercase 6-digit hex canonical). Balanced pairs are swapped for `<u>`/`<span style="color:…">` AFTER sanitization (the ⟦FN⟧ pattern) by `replaceInlineSentinels` in chapterMarkdown.js / `_apply_inline_sentinels` in output_formatter.py; unmatched/misnested/invalid markers stay literal (and block saves via `sentinel:literal`). Markers inside code spans stay literal; a pair may span a whole code span; pairs never cross block boundaries. bbcode.js maps them to `[U]`/`[COLOR=#hex]`. Editor: StarterKit Underline (Ctrl+U) + TextStyle/Color (`@tiptap/extension-text-style`), color picker in MarkButtons; `FlexibleCode` overrides TipTap Code's `excludes:"_"` so code spans keep coexisting marks. Bare URLs containing marker brackets are rejected by `md.validateLink` (styled-but-unlinked) — linkify would otherwise swallow `⟦/U⟧` into the href.

### Chapter publishing (drafts / scheduling)
Per-chapter visibility via `chapters.published_at` (migration 11): **NULL = draft, future = scheduled, past = live**. Visibility is evaluated at query time (`published_at <= now`), so scheduled chapters appear automatically — no cron. All pre-existing chapters were backfilled to their translation_date (everything stayed live).
- **Defaults**: translation-pipeline chapters publish immediately; original-work chapters are born drafts. The "Save as draft(s)" checkbox on the Dashboard and Queue pages overrides the pipeline default (`save_as_draft` run option → `save_chapter(publish=False)`). Re-saves/retranslations never change publish state.
- **Endpoints**: `PUT /api/books/{id}/chapters/{n}/publish` `{published_at: iso|null}` (now / schedule / null=unpublish — no boolean, the timestamp is the whole state); `POST /api/books/{id}/chapters/batch-publish` `{chapters, published_at?, interval_hours?, unpublish?}` — interval staggers ascending chapters for a drip release. Timestamps are naive server-local ISO (matches translation_date; tz-aware inputs converted).
- **Public gating**: `published_only=True` param on `list_chapters`/`get_chapter`/`get_chapters_bulk`/`search_book_chapters` (public.py passes it everywhere), RSS feeds gated in `list_recent_translated_chapters`, public Library uses `published_chapter_count`/`last_published_date` from list_books, comments on unpublished chapters 404, public EPUB generates published-only and regenerates when a scheduled chapter crosses its publish time (version basis = max(modified_date, latest_published_at)).
- **UI**: PublishMenu chip (Draft/Scheduled/Published + publish-now/schedule/unpublish) in both editors' headers (`web/frontend/src/components/PublishMenu.jsx` — note `localIso()`: never use `toISOString()`, UTC breaks the naive-local comparisons); draft/scheduled chips on Books chapter rows; batch **Publish** action on Books with `BatchPublishModal` (start time + stagger interval).

### Queue Processing
- Batch processing of multiple files/chapters
- Resume functionality for interrupted translations
- Metadata preservation for book/chapter association

### Entity notes (model-updatable, versioned)
An entity's `note` is standing guidance injected into every prompt that mentions the entity (`entities_inside_text` attaches it). The model may **write a note on any new entity** — as many per chapter as it judges useful — and may **update a note on an entity it already knows** whenever a fact in it has moved on: an age after a time skip, a cultivation realm after a breakthrough, a rank/sect/office after a promotion or defection, a shifted allegiance. A stale note is worse than none, because every later chapter is translated against it.

- **Channel**: a top-level `note_updates` object in the response JSON, a sibling of `entities`, keyed by the untranslated entity text: `{"陈元": {"note": "complete replacement", "reason": "why"}}`. Kept separate from `entities[...]["note"]` because the model re-emits known entities routinely; the precedence rule still stands (now in `ui.py::_write_entity_note` rather than a SQL COALESCE), so a volunteered note on the entity channel can never clobber a curated one.
- **Rules are injected from code**, not the prompt corpus — `generate_system_prompt` appends the ENTITY NOTES section and `_build_response_template` adds the `note_updates` example. Per-book templates are frozen copies, so a prompt-file-only feature would reach only books created afterwards. `providers/gemini_provider.py::_create_response_schema` carries the channel too (structured output would otherwise make it unusable).
- **Guards** (`TranslationEngine.validate_note_updates`): the key must already be an entity of this book (the channel cannot create entities), no-ops and empty notes are dropped, notes cap at 500 chars, at most **5 updates per chapter** (`NOTE_UPDATE_MAX_PER_CHAPTER`, sized for a time-skip chapter that ages several characters at once; new-entity notes are uncapped), and a note losing >50% of its length is flagged `shrink` for the audit UI. None of this blocks a bad rewrite — versioning does.
- **Two application paths**: with entity review on, updates ride the `entity_review_needed` handshake as their own section (old note vs proposed, editable, reject per row; an edited note is attributed to `human`). With `no_review`, validated updates apply immediately and are audited afterwards. Skip Review applies none.
- **Everything is versioned**: `entity_note_revisions` (migration 18) stores previous_note/new_note/author(`model`|`human`|`script`)/chapter/reason/shrink, pruned to 20 per entity. Endpoints: `GET /api/entities/note-revisions?book_id=&entity_id=`, `POST /api/entities/note-revisions/{id}/revert` (the revert is itself recorded). UI: "Recent note changes" panel on the Entities page.
- ⚠️ **`db/entities_repo.py::set_entity_note` is the only sanctioned note write** and nothing may bypass it, or the history stops being reconstructable. `add_entity`, `update_entity` and `update_entity_by_id` divert a `note` argument to it (with `note_author`/`note_chapter`/`note_reason`), `ui.py::_write_entity_note` handles the entities-channel writes during translation (creation included — that used to be a raw INSERT with the note inline), and `substitute_in_entity_notes` records as `script`. Never add a `note = ?` UPDATE anywhere else; `tests/test_entity_note_precedence.py` guards ui.py against exactly that.
- **Creation is a revision too** (`previous_note IS NULL`), which is what makes point-in-time reconstruction possible: `notes_as_of(book_id, chapter, entity_ids=None)` → `{entity_id: note_or_None}`, the glossary as it read at the **end** of that chapter. It rewinds to the `previous_note` of the earliest revision made after that chapter; revisions with no chapter (hand edits, script sweeps) belong to the present and are not rewound; `entities.origin_chapter` acts as a floor so an entity that didn't exist yet reports no note. Legacy caveat: notes written before migration 18 have no creation row, so their earliest recorded state is the first revision's `previous_note` (book 90's pre-ch243 note history is genuinely unrecoverable).
- **Retranslation gets the notes of its own time**: `TranslationEngine.apply_historic_notes` rewinds the run's entity snapshot to `notes_as_of(book_id, chapter)` before the prompt is built, so retranslating ch34 doesn't feed it ch300 facts. Gated by one indexed check (`has_note_revisions_after`), so a new head chapter is a no-op. Entity *translations* are never rewound — renderings must stay consistent book-wide. A run whose notes were rewound also has its `note_updates` **suppressed**: its view of the notes is stale, so an update from it would regress the live note and would land out of chapter order in the history the rewind depends on.
- **`get_entities.py` reads the glossary at a point in time**: `--origin-chapter 1-20` shows notes as they read at ch20 (`--as-of-chapter N` to set it explicitly, `--current-notes` to opt out), and the chapter filter matches entities whose *note changed* in the range as well as those introduced in it — tagged `note updated chN` in the output, `--origin-only` for the old behaviour. Without this, reviewing ch240-260 missed every long-lived entity whose guidance moved during it.
- **Kill switch**: `entity_note_updates` in settings.json (default on, Settings → Entity Notes).
- Two-pass books do this in pass 1 (`extract_entities(..., return_note_updates=True)`), applied before pass 2 builds its prompt so pass 2 sees the corrected note.

### The response contract is code-owned (`prompt_contract.py`)
The JSON shape the translation model must return is **not** in the prompt corpus. It is assembled by `prompt_contract.response_contract_section()` and appended by `generate_system_prompt` as a `RESPONSE FORMAT:` section, exactly the way ENTITY NOTES is — and for the same reason: `books.prompt_template` is a **frozen copy** taken at book creation, so anything stated only in prompt text runs stale forever. When `note_updates` shipped, 44 of the 67 stored prompts never learned about it.

- **Code-owned**: the `++++ Response Template` block, "Output must be valid JSON…", the entity-key rule, the `"exact"`/`"similar"` explanation, the per-entity field list, and the entity-`note` bullets. **Stays editable**: CRITICAL RULES, TRANSLATION GUIDELINES, `{{ENTITY_CATEGORIES}}`, what-counts-as-an-entity, and BOOK-SPECIFIC NOTES.
- **`strip_legacy_contract(prompt)`** removes the pre-extraction wording at assembly time, on every prompt, idempotently — so a book restored from an old backup or hand-edited back still assembles correctly. It **never touches anything below the BOOK-SPECIFIC NOTES header**; two books repeat contract wording inside their notes and `legacy_in_notes()` reports them rather than editing a human's text.
- **`backfill_prompt_contract.py --all [--dry-run]`** cleaned all 67 stored prompts (2026-08-23). Re-runnable; reports any book whose wording didn't match a *core* pattern (`CORE_PATTERN_NAMES`) — the optional ones are absent from most books by design.
- **The section is mode-aware**: `translate_only` drops every entity rule, `entity_only` drops the prose fields, and Gemini gets the prose without the `++++` example (its `responseSchema` supersedes it). Gender is rendered from the book's own `gendered_categories`, not the corpus's hardcoded "For characters only".
- ⚠️ **Entity categories moved to `genres.json`** (`genre_categories(genre, prompt_text=None)`). They used to be reverse-engineered from the `++++` block in the genre prompt at book creation, which stopped working once that block left the corpus. The genre list is only a **starting default** — `set_book_categories` writes it once at creation and `books.categories` governs from then on. Prompt parsing is kept as a fallback for a hand-written prompt file that still carries a template block.
- `GENRE_EXAMPLES` (keyed by `books.source_language`, not a genre column — there isn't one) holds each genre's example title/summary/content **and entity examples**; the latter matter because a book with an empty glossary would otherwise show the model `"示例characters": "Example Character"`.

### Footnote candidates during translation (`scan_mode`)
The footnote-candidate scanner can run **inside the translation pass** instead of as a second model call. Same rules, same anchoring filter, same review queue — only the delivery changes. Per book, via the Footnote Candidate Scanner module's **`scan_mode`** setting:
- **`ingest`** (default) — today's behaviour: `event_new_chapter_saved` enqueues a separate scan on the module's own model (`deepseek:deepseek-v4-pro`).
- **`translation`** — the translator returns a top-level `footnote_candidates` array, a sibling of `entities`. The on-ingest worker short-circuits, so no chapter is scanned twice.

Global kill switch: **`footnote_inline_scan`** in settings.json (default on, Settings → Footnote Candidates). A book with **no `scan_mode` row** resolves to `ingest` — switching one over means actually saving the setting in Book → Modules → Footnote Candidate Scanner, not just flipping the global switch.

⚠️ The `model` field is deliberately **not** hidden behind `show_if` in inline mode: `BookModulesModal.handleSave` strips hidden fields, so hiding it would delete a book's chosen scan model on the switch to inline and silently fall back to the default on the way back.

- **The scan prompt is split the same way as the translation contract**: `footnote_scan_core.SCAN_RULES` is the editable half (the module textarea's `default_text`), and `OUTPUT_STANDALONE` / `OUTPUT_INLINE` are appended from code. `SYSTEM_PROMPT = SCAN_RULES + "\n\n" + OUTPUT_STANDALONE` is byte-identical to what it always was, so `--print-prompt` and the CLI are unchanged. `scan_rules()` strips an output paragraph left in a prompt customised before the split — both the stock wording and the Markdown `# OUTPUT` form book 79 rewrote it into.
- **What the inline prompt injects is only the exclusion list.** The translator already holds the source, the glossary (PRE-TRANSLATED ENTITIES) and the book's conventions; only "ALREADY FOOTNOTED in this book" is missing. Built **once per chapter** by `inline_scan_section()` and passed to `generate_system_prompt` as `footnote_section` — that method runs again for every chunk, and the lookup is two queries.
- **Two-pass books collect in pass 1** (`extract_entities`, `mode='entity_only'`), which is unchunked and so sees the whole chapter; pass 2 is `translate_only` and carries no channel for them. `extract_entities(return_note_updates=True)` now returns a **3-tuple**.
- **Chunks concatenate, not merge** (`combine_json_chunks`) — candidates are per occurrence, not per key.
- **First-mention dedupe is enforced in code, not just asked of the model.** `persist_inline_candidates` filters three times: anchorable in this chapter's source, first mention within the chapter, and — via `prior_mention_keys()` — not a term the book already settled at an *earlier* chapter (a real footnote, or a candidate of any status, rejections included). The ALREADY FOOTNOTED prompt block only *asks*; the bulk CLI backstops it with a `dedup_candidates` sweep at the end of a run, and the inline path has no run to sweep. Reading order decides: a later chapter's find never suppresses an earlier one, and a chapter never suppresses itself on retranslation.
- **Yield falls with chapter number in some books, not all** (measured 2026-08-23): book 8 collapses to 0.00/chapter by ch800, book 35 decays late, book 90 drops to 1.38 in its tail — but book 79 stays flat near 2.4 over 900 chapters and book 15 *rises*. Settings that keep importing new real-world referents don't exhaust. **Judge a chapter's yield against its own book's local rate, never the book-wide mean.**
- **Persisted after `save_chapter`** (`ui.py::_store_footnote_candidates`), because the chapter number is only settled there — it can be model-detected. Failures are logged and swallowed.
- ⚠️ **`record_footnote_scan(..., preserve_reviewed=True)`** keeps accepted/rejected rows and replaces only pending ones, dropping an incoming duplicate of a decided term. Inline scanning re-runs on **every retranslation**, so without it a keep/reject decision would not survive one. `footnote_scan.py --force` passes it too — it had the same footgun.
- **Cost**: the scan rules ride along in *every chunk* of the translation prompt and are billed at the translation model's rate, in exchange for not re-sending the chapter, the glossary and the book's notes. Measured on book 90 ch200: +7.8k chars, 6.4% of that prompt. Roll out one book at a time.

### Concurrent translations (one job per book)
The web GUI runs **several books at once**. Chapters *within* a book stay
sequential — the entity glossary is built incrementally, so two chapters of one
book in flight would corrupt it — but different books share nothing.

- **`web/services/job_manager.py`** holds three objects: `JobHub` (process-wide —
  WebSocket registry, replay buffer, activity log), `Job` (one per book — status,
  the entity-review / JSON-fix / chapter-conflict handshakes, cancel flag,
  auto-process counters) and `JobRegistry` (`{book_id: Job}` plus the cap,
  modelled on `modules/task_runner.ModuleTaskRunner`). `Job` delegates every hub
  method and keeps the pre-split member names, so `web_interface.py` and `ui.py`
  are unchanged.
- **Every WS message is stamped** with `book_id`/`job_id` by `Job.send_message_sync`,
  which is how the frontend routes events. An explicit `book_id` already in the
  payload wins (`chapter_conflict_needed` names the *incoming* chapter's book).
  The replay buffer collapses terminal events per `(type, book_id)`, so one
  book's completion can't evict another's.
- **Cap**: `max_concurrent_translations` (settings.json, default 3), read live at
  job start — changing it in Settings needs no restart. Over-cap or same-book
  starts return 409. `MYSQL_POOL_SIZE=20` in `.env` covers the extra workers.
- **Per-run state**: each job builds its own `TranslationConfig` clone,
  `TranslationEngine` and `WebInterface` via `make_web_interface()` in
  `web/app_factory.py`. Per-request model overrides used to be written onto the
  shared config and undone in a `finally` — non-reentrant, and `max_chars`
  (chunking) is re-derived from the model mid-run.
- ⚠️ **Entity isolation is the load-bearing part.** `DatabaseManager.entities` is
  one cache holding one book at a time. The translation path must use
  `get_entities_snapshot(book_id)` (`db/entities_repo.py`), which returns a fresh
  dict without touching that cache; the cache remains for the admin
  Entities/Dictionary pages. Never reintroduce `entity_manager.entities` reads
  into `translation_engine.py` or `ui.py`.
- **Endpoints**: `GET /api/translate/jobs` (per-book list the UI hydrates from),
  `POST /api/queue/process-all` (one worker per queued book, up to the cap).
  Control endpoints (`cancel`, `submit-review`, `skip-review`,
  `submit-json-fix`, `resolve-chapter-conflict`, `stop-auto`) take `book_id`;
  omitted means "the only job parked on this prompt" (or all, for cancel/stop).
  `GET /api/translate/status` keeps top-level `status`/`is_running`/`auto_process`
  as **aggregates** because `translation_status.py` reads them, and adds a
  per-book `jobs` map.
- `process-next` with no `book_id` picks the earliest-queued book that isn't
  already running (`get_next_queued_book_ids()`), and the auto-process loop stays
  pinned to that book.
- **Frontend**: `lib/jobs.js` (pure reducer) + `hooks/useJobs.jsx` (one shared
  context — Dashboard and Queue no longer keep separate copies),
  `components/jobs/` (one card per running book, reusing `TranslationProgress`),
  and `PromptHost` mounted in `Layout` so a book needing a decision reaches the
  user on any page. Debug prompt dumps are per book: `prompt-book<N>.tmp`.

### Configuration

**Two-tier storage:**
- **`settings.json`** (project root, gitignored) — runtime-mutable, non-secret app settings (models, site branding, toggles, WP url/username, etc.). Managed via `settings_store.py`. The Settings UI writes here. Schema and defaults are in `settings_store.SCHEMA`. On startup, values are mirrored into `os.environ` so existing `os.getenv()` callers keep working without per-call refactoring.
- **`.env`** — true secrets and infrastructure (API keys, `WP_APP_PASSWORD`, `ADMIN_PASSWORD`, `SECRET_KEY`, `MYSQL_*`, `DB_BACKEND`, `CF_*`, `IPINFO_API_KEY`). The Settings UI's provider-key fields and the WordPress app-password field still write here via `_persist_env()`.

First-run migration: if `settings.json` doesn't exist, `settings_store.load()` seeds it from current `os.environ` values, so upgrading an existing deployment is a no-op.

Environment variables in `.env`:
- `OPENAI_KEY`: OpenAI API key
- `DEEPSEEK_KEY`: DeepSeek API key  
- `ANTHROPIC_KEY`: Anthropic Claude API key
- `GOOGLE_AI_KEY`: Google Gemini API key
- `XAI_KEY`: xAI (Grok) API key
- `META_KEY`: Meta Model API key (Muse Spark)
- `MOONSHOT_KEY`: Moonshot AI (Kimi) API key
- `TRANSLATION_MODEL`: Default model (format: provider:model)
- `ADVICE_MODEL`: Model for entity advice
- `MAX_CHARS`: Legacy fallback for chunk size (now per-provider via models.json)
- `DEBUG`: Enable debug logging

Comment-system env vars (chapter comments feature):
- `CF_TURNSTILE_SITE_KEY` / `CF_TURNSTILE_SECRET_KEY`: Cloudflare Turnstile (already used by recommendations form)
- `CF_API_EMAIL` / `CF_API_KEY`: Cloudflare Global API credentials for IP-ban edge enforcement. **Copy from `~/scripts/.env::CF_EMAIL` and `~/scripts/.env::CF_GLOBAL`** (one-time). Without these, comment IP bans still take effect in our DB but won't be pushed to the Cloudflare edge.
- `COMMENT_AUTOMOD_ENABLED`: `1` to enable async AI auto-moderation of new comments (default `0`)
- `COMMENT_AUTOMOD_MODEL`: Model spec for auto-mod (default `claude:claude-haiku-4-5`)

Reply-notification emails have two pluggable backends (`web/services/email_sender.py`), selected by `EMAIL_BACKEND` (`ses` default | `postfix`). SES is the API path via boto3 `send_raw_email`; Postfix is the local-MTA fallback on `localhost:25` (kept for instant, code-free rollback). SES falls back to Postfix automatically when its credentials are absent.
- `EMAIL_BACKEND` (settings.json / `EMAIL_BACKEND` env): `ses` or `postfix`. Editable in Settings UI.
- `EMAIL_FROM`: Sender address for reply notifications (e.g. `editor@boondollars.com`). For SES it must be a verified SES identity (domain or address); for Postfix a domain it's authorized to send from. If unset, notifications are sent from `noreply@localhost` and won't deliver — explicitly set this before turning the feature live.
- `SITE_BASE_URL`: Public base URL of the reader site (e.g. `https://reader.boondollars.com`), used to build absolute links to chapters and the unsubscribe endpoint in outgoing emails. Must be set or email links will be relative and break in many mail clients.
- **Amazon SES secrets/infra (`.env` only, not in settings SCHEMA — same convention as the Spaces `BUCKET_*` vars):** `SES_REGION` (default `us-east-2`), `SES_ACCESS_KEY_ID`, `SES_SECRET_ACCESS_KEY` (IAM user with `ses:SendRawEmail`). The sending identity must be verified in that region and the account out of the SES sandbox to reach arbitrary recipients.

### Traditional → Simplified Chinese preprocessing
Optional pre-translation step that converts traditional Chinese characters to simplified using OpenCC (`t2s` config, in `trad_simp.py`). Useful when a mirror serves a mainland novel in traditional glyphs (張羽 instead of 张羽), which breaks entity matching and prompt consistency.

**Do not use `tw2sp` here.** These raws are mainland novels mechanically rendered into traditional characters — the vocabulary underneath is already mainland, so there are no Taiwan phrases (軟體/螢幕/解析度) to undo, and `tw2sp`'s phrase layer corrupts correct text: 什么→什幺, 抬→擡, 核心→内核, 智慧→智能, 程序→进程, 建立→创建, and 位元婴→比特婴 (matching 位元 as the computing term "bit" across a word boundary).

`t2s` alone cannot resolve two morpheme splits, so `trad_simp.py` adds guardrails:
- **乾** as *gān* ("dry") → 干 (乾净→干净), but as the *qián* trigram it stays 乾 in simplified. OpenCC protects the phrase 乾坤 but not proper nouns, so `PROTECTED_TERMS` shields names like the dynasty 大乾.
- **著** as the aspect particle → 着 (看著→看着), but as *zhù* ("notable/author") it stays 著. `t2s` leaves every 著 alone, so the converter rewrites them and spares `ZHU_WORDS` (著名, 著作, 显著, 土著, …).

Covered by `tests/test_trad_simp.py`.

- **Global default**: `TRAD_TO_SIMP=1` env var (default `0`). Toggle in Settings page → "Convert traditional Chinese to simplified".
- **Per-book override**: `books.trad_to_simp` column is tri-state — `NULL` inherits the global default, `0` forces off, `1` forces on. Set on the book edit form.
- **Where conversion runs**: inside `database.py::save_chapter` and `database.py::add_to_queue`. The stored source is rewritten before persistence; reading is unaffected. Idempotent — already-simplified text passes through unchanged.
- **Retrofitting existing chapters**: `python3 bulk_convert_trad_to_simp.py --book-id N [--dry-run]` rewrites stored `untranslated_content` for chapters saved before the toggle was flipped on.
- **Dependency**: `OpenCC` (pip) + `libopencc1.1`/`libopencc-data` (apt). Imported lazily — never loaded unless the feature is actually triggered.

### Database backups
Daily `mysqldump` of the `t9` database to a **private DigitalOcean Space**, run from
this VM (the MySQL grant is host-restricted — `t9@localhost` on the db host is denied,
so dumps cannot run db-side). Cron: `30 3 * * * /home/mdm/t9/backup_mysql.sh`.

- **`backup_mysql.sh`** — dumps → `backups/<db>-<ts>.sql.gz` (atomic `.partial` + `mv`),
  uploads, prunes the bucket, then keeps only the newest dump locally.
- **`backup_spaces.py`** — the storage layer (`list` / `upload` / `fetch` / `prune`).
  Standalone boto3: it deliberately imports **no** app modules, so a backup still runs
  on a day when `config`/`settings_store` won't import.
- **`restore_mysql.sh`** — `--list`, `--fetch latest`, `--restore latest --yes-really`.
  `--fetch` verifies the sha256 recorded on the object at upload time.

⚠️ **Never route a dump through `spaces.py`.** That module serves covers/illustrations/
EPUBs and hardcodes `ACL="public-read"` on both `upload()` and `upload_bytes()` — a dump
sent through it is world-readable on the CDN. `backup_spaces.py` uploads `ACL="private"`
to a bucket with no CDN attached.

**Retention.** 14 newest dumps, plus the earliest dump of each of the last 6 calendar
months (~20 GB at ~1.05 GB/dump). The month is derived from the timestamp in the key,
not the object's `LastModified`. **Fail-safe ordering is load-bearing**: the local prune
runs only after a verified upload (the helper HEADs the object and compares byte counts),
so a failed upload keeps every local copy and exits nonzero. A failed *bucket* prune is
only a warning — it must never skip the local prune, or the VM fills up.

**Config lives in `.env` only** — `BACKUP_BUCKET`, `BACKUP_BUCKET_REGION`,
`BACKUP_BUCKET_ENDPOINT`, and optionally `BACKUP_BUCKET_ACCESS_ID`/`BACKUP_BUCKET_SECRET`
(falling back to `BUCKET_ACCESS_ID`/`BUCKET_SECRET`). Per `settings_store.py`, these must
**not** go in `SCHEMA`: SCHEMA keys are mirrored from `settings.json` into `os.environ`
and would clobber `.env`.

**History.** Until 2026-09-06 backups were scp'd to the db host (7 days there, newest-only
on the VM). That put 9 GB of backups on the 24 GB disk of the one machine whose loss they
exist to survive. The db host now holds none.

### Provider Configuration
The provider system is configured via `providers/models.json`:
- **providers**: Map of provider configurations with API settings and per-provider max_chars
- **aliases**: Short names for providers (e.g., "oai" → "openai", "gemini" → "google")
- **max_chars**: Per-provider chunk sizes optimized for each model's capabilities
- **Extensible**: Add custom providers by editing the JSON file

### 529 "Overloaded" retry
When a provider returns a transient 529 "Overloaded" (the Claude Code CLI prints `API Error: 529 Overloaded`; the Anthropic SDK raises with status 529), the translation engine no longer treats it as a JSON-parse failure. Instead it waits a configurable interval and retries — looping until the service recovers, **without** consuming the per-chunk retry budget (which is reserved for genuine parse/connection errors).
- **Setting**: `overload_retry_wait_seconds` (settings.json, default `300`), mirrored to env `OVERLOAD_RETRY_WAIT_SECONDS`.
- **Detection**: `providers.base.looks_overloaded()` + `OverloadedError`; providers raise `OverloadedError`, and the engine also sniffs response text / exception strings as a safety net. Applies to streaming, non-streaming, and entity-extraction calls.

### Supported Providers
- **OpenAI**: GPT-4, GPT-3.5-turbo, etc. (max_chars: 5000)
- **DeepSeek**: deepseek-chat (via OpenAI-compatible API) (max_chars: 5000)
- **Anthropic Claude**: Claude 3.5 Sonnet, Claude 3 Opus, Claude 3 Haiku (max_chars: 8000)
- **xAI**: grok-4.5 (default), grok-4.3, grok-4.20 variants — OpenAI-compatible chat-completions API at api.x.ai, reuses OpenAIProvider (max_chars: 8000, alias `grok`, key: `XAI_KEY`)
- **Meta**: muse-spark-1.1 — OpenAI-compatible Meta Model API at api.meta.ai, reuses OpenAIProvider (max_chars: 12000, alias `muse`, key: `META_KEY`; 1M context / 131k max output)
- **Moonshot**: kimi-k3 (default) — OpenAI-compatible API at api.moonshot.ai, reuses OpenAIProvider (max_chars: 8000, alias `kimi`, key: `MOONSHOT_KEY`)
- **Google Gemini**: Gemini 2.5 Flash, Gemini 2.5 Pro, Gemini 1.5 Pro/Flash (max_chars: 6000)
- **Custom**: Easy to add new providers via configuration

## Development Notes

### Dependencies
Install with: `pip install -r requirements.txt`
Key packages: openai, anthropic, google-genai, ebooklib, beautifulsoup4, questionary, rich, pyperclip

### Provider System Development
To add a new AI provider:
1. Create a new provider class inheriting from `ModelProvider` in `providers/`
2. Implement required methods: `chat_completion`, `get_response_content`, etc.
3. Add provider configuration to `providers/models.json`
4. Update `providers/factory.py` to include the new provider class

Example provider configuration:
```json
{
  "providers": {
    "newprovider": {
      "class": "NewProvider",
      "base_url": "https://api.newprovider.com/v1/",
      "api_key_env": "NEWPROVIDER_KEY",
      "default_model": "new-model-v1",
      "max_chars": 5000
    }
  }
}
```

### Database Migration
The system automatically migrates from legacy JSON entity storage to SQLite on first run.
Database file: `database.db` (migrated from `entities.db`)

### Error Handling
Extensive error handling for API failures, file I/O, and database operations. Check logs in debug mode for troubleshooting.

### Testing
No formal test framework currently. Manual testing via CLI commands recommended.

## Recent Improvements (2025-05-30)

### Per-Provider Configuration
- **Moved MAX_CHARS to per-provider basis**: Each provider now has optimized chunk sizes in `models.json`
- **Provider-specific max_chars**: OpenAI/DeepSeek (5000), Claude (8000), Gemini (6000)
- **Legacy fallback**: Global MAX_CHARS environment variable still supported as fallback

### Gemini Provider Enhancements
- **Comprehensive safety settings**: All 5 core Gemini harm categories set to `BLOCK_NONE` to minimize content blocking
- **Removed token limits**: No max_tokens constraint, allowing full ~64k model capacity
- **Cleaned up JSON processing**: Removed problematic JSON cleanup/repair methods, relying on native structured output
- **Improved error handling**: Proper finish reason mapping for better debugging
- **Full streaming support**: Real-time token generation with progress indicators
- **Schema conflict resolution**: Gemini-specific logic to remove JSON schema templates when using structured output

### Template System Improvements
- **Fixed critical entities bug**: `{{ENTITIES_JSON}}` replacement now works correctly for both custom and default system prompts
- **Gemini schema compatibility**: JSON schema template sections are automatically removed for Gemini providers to prevent conflicts with native structured output
- **Template preprocessing**: Improved system prompt generation with provider-specific optimizations

### OpenAI Provider Bug Fixes
- **Parameter filtering**: Provider-specific config parameters (max_chars, default_model, models) no longer passed to OpenAI client constructor
- **Token parameter handling**: Removed hardcoded max_tokens to use model defaults, resolving compatibility with o3-mini and other reasoning models
- **API compatibility**: Fixed support for newer OpenAI models that use different parameter names

### Gemini-Specific Notes
- **Safety Categories Covered**: HARASSMENT, HATE_SPEECH, SEXUALLY_EXPLICIT, DANGEROUS_CONTENT, CIVIC_INTEGRITY
- **Models Supported**: gemini-2.5-flash-preview-05-20, gemini-2.5-pro-preview-05-06, gemini-1.5-pro/flash variants
- **Structured Output**: Uses native Gemini response schemas for JSON generation
- **No Token Limits**: Removed artificial 8192 token constraint to use full model capacity

### Performance Optimizations  
- **Provider chunking**: Each model uses optimal chunk sizes for its capabilities
- **Streaming improvements**: Better progress tracking and token counting
- **Error resilience**: Improved safety filter and token limit handling
- **API compatibility**: Better handling of model-specific parameter requirements

### Testing Status
- **OpenAI o3-mini**: ✅ Working correctly with parameter fixes
- **Claude Sonnet**: ✅ Provider initialization and API calls working
- **Gemini**: ✅ All safety settings and schema handling working
- **Entity management**: ✅ Proper JSON replacement and consistency maintained

### Code Refactoring (2025-05-30)
- **Database Management Refactor**: Renamed `EntityManager` class to `DatabaseManager` to better reflect its expanded responsibilities
- **File Reorganization**: Renamed `entities.py` to `database.py` for clearer module purpose
- **Database File**: Updated from `entities.db` to `database.db` for consistency
- **Scope Expansion**: Class now manages entities, books, chapters, and all database operations in a unified interface
- **Backward Compatibility**: Variable names like `entity_manager` remain unchanged for consistency