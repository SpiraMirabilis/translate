# Code Review Punch List — 2026-07-10

> ## ✅ SWEEP COMPLETE 2026-07-10 (same day, second pass)
> Every 🔴/🟠/🟢 item below was fixed except the explicitly-deferred project-sized
> ones, marked ⏳ in place. Verified: full pytest suite (463) green, frontend vitest
> (238) green, `npm run build` clean. **Service NOT restarted** (a translation was
> running) — backend changes go live on the next `systemctl --user restart t9.service`.
>
> Deploy follow-ups that need the user:
> 1. ~~Apache reader vhost~~ **DONE 2026-07-10**: mod_remoteip + CF ranges were
>    already live globally (conf-enabled/remoteip.conf); the missing piece —
>    `RequestHeader unset CF-Connecting-IP` — was added to reader.conf + t9.conf
>    (backups: *.bak-20260710 in sites-available), configtest + graceful reload,
>    verified: forged CF-Connecting-IP/XFF from a non-CF peer never reaches the
>    backend, real CF reader traffic still resolves correctly.
> 2. `git add confusables_skeleton.json` — ships the twkan cache so fresh deploys
>    never fetch from unicode.org at ingest (the module now degrades gracefully
>    either way).
> 3. Migration 14 (chapters.translation_date index) applies on next app start.
>
> Deferred (⏳, discussed in review notes): cross-renderer parity test harness,
> public/admin process split, BBCode-native storage, full table/TOC windowing,
> "pin the bar" e-ink setting, drawer focus traps, remaining python-markdown vs
> markdown-it engine-level gaps (list-interrupting-paragraph, `1)` lists, `#Heading`).

Remaining findings from the six-agent codebase review. Everything already fixed that day
(the four crashers, ebook atomicity/locking + admin-export draft leak, ChapterEditor nav
reset, WS replay gating, zombie reconnect, unit-converter hyphen ranges, sentinel-table
spacing guard, strikethrough + nested-list parity, OpenAI provider legacy-gate removal,
repo hygiene) is **not** listed here. Items are `file:line` as of that review; lines will
drift.

Legend: 🔴 fix soon · 🟠 worth scheduling · 🟢 when convenient

---

## Security (internet-facing reader)

> ✅ **Section complete 2026-07-10.** ip.py trusted-proxy gate + IP validation, Apache
> mod_remoteip config; recommendations field caps/URL-scheme/email checks (per user:
> admin message content is trusted, no verification flow needed); SVG served with CSP
> sandbox + attachment; persist_env newline-reject/quote/chmod 600; central header
> sanitizer in email_sender; unsubscribe = GET confirm page + POST action; email_tokens
> refuses dev-secret when EMAIL_FROM set; export HTML + EPUB titles escaped;
> upload_cover PIL-verified; prewarm lock moved under project dir.

- 🔴 **Spoofable client IP** — `web/services/ip.py:18-25` trusts `CF-Connecting-IP` then
  first `X-Forwarded-For` element unconditionally. If the origin is ever reachable
  off-Cloudflare: unlimited rate-limit evasion, comment-ban evasion, poisoned
  `reader_log` (strings fed to `gethostbyaddr` and the admin UI), framing an innocent IP
  for a CF edge ban. Verify Apache strips/overwrites these headers unless the peer is in
  CF ranges; consider an app-side allowlist.
- 🔴 **Anonymous outbound-email relay** — `web/api/recommendations_public.py:33-75`: no
  field length caps, `"@" in` as the only email validation, and the server emails an
  unverified third-party address with attacker-chosen content. SES-reputation burner.
  Add `max_length` on every field, `^https?://` check on `source_url`, and don't email
  until the address is verified.
- 🔴 **Stored XSS via SVG illustrations** — `illustrations.py:40,84-92` accepts
  `image/svg+xml` from imported EPUB/FB2; served same-origin via `FileResponse` when
  Spaces is off (`web/api/public.py:365-376`). A script-bearing SVG executes on the
  reader origin. Drop SVG / rasterize on import / serve with CSP sandbox + attachment.
- 🟠 **`.env` line injection** — `settings_store.py` `persist_env` writes
  `f"{key}={value}"` unsanitized (reached from `settings_api.py:59` and
  `wordpress_api.py:74`). A value containing `\n` injects lines that override
  `ADMIN_PASSWORD`/`SECRET_KEY`/`DB_BACKEND` on next restart. Also: values are unquoted
  (a `#` or space corrupts the file) and no restrictive perms are set on create.
  Reject/strip `[\r\n]`, quote values, chmod 600.
- 🟠 **CR/LF in user fields crashes email construction** —
  `web/services/recommendation_emails.py:193`, `email_sender.py:122`,
  `notifications.py:82`: `EmailMessage` raises `ValueError` on newline header values;
  BackgroundTask sends die silently, the sync admin "email requester" 500s permanently
  for that record. Strip `[\r\n\x00]` from all header-bound fields.
- 🟠 **GET unsubscribe fires on mail-scanner prefetch** —
  `web/api/comments_public.py:378-397`: SafeLinks-style gateways follow the link and
  suppress ALL email types for that address. Serve a confirm page whose button POSTs.
- 🟢 **`dev-secret` HMAC fallback** — `web/services/email_tokens.py:25`: unsubscribe
  tokens and reply-correlation HMACs are forgeable if `SECRET_KEY`/`T9_PASSWORD` are
  both unset. Refuse to sign with the fallback outside dev.
- 🟢 **Export HTML unescaped titles** — `web/services/exporters.py:180-210`: book/author/
  chapter titles interpolated raw into export HTML ("Q&A" breaks; markup executes in the
  downloaded artifact). Same hole in `output_formatter.py` EPUB metadata
  (`:459-465,522-533,671-680,847-877`) — invalid XHTML breaks strict readers.
- 🟢 **`source_url` rendered as clickable admin link with no scheme check** —
  `recommendations_public.py:69` + admin UI. One-line `^https?://` server check.
- 🟢 **`upload_cover` trusts client Content-Type** — `web/api/books.py:521-529`.
  Admin-only; verify with PIL before saving.
- 🟢 **Predictable /tmp lock file** — `prewarm_ebooks.py:54`: any local user can flock it
  (silent disable) or symlink-truncate. Move under the project dir or `/run/user`.

## Data integrity

> ✅ **Section complete 2026-07-10.** sync_databases introspects both schemas and
> hard-fails on mismatch; renumber_chapter covers chapter_revisions + polish_jobs;
> import_from_json does explicit update-else-insert (upsert_entity_sql removed);
> delete_chapter enables FKs; delete_entity/change_entity_category/
> get_entity_by_translation take book_id scoping (CLI call sites wired); queue-
> supplied chapter number wins over model-reported; --queue no longer stores the
> book title as chapter title; merge strips the common prefix (translates only the
> appended segment, full-translate w/o stash when not a clean append); queue
> position computed inside INSERT; _m002 numeric-guarded on SQLite; _m001 only
> swallows duplicate-index errors; footnote def-block must be 1..N and only
> defined marker numbers are stripped.

- 🔴 **`sync_databases.py` is ~10 migrations stale** — `TABLES` at `:28-81` omits
  `published_at` (synced chapters all become drafts → public site empties),
  `is_original`, `view_count`, `trad_to_simp`, `tags`, `modules`, etc., and whole tables
  (comments, footnotes, revisions, recommendations, reader_log…) while the destination
  is truncated. Data-loss trap. Introspect columns or hard-fail on mismatch.
- 🔴 **`renumber_chapter` misses `chapter_revisions` + `polish_jobs`** —
  `db/chapters_repo.py:811-875`: revision history detaches; a later chapter reusing the
  number inherits the old chapter's snapshots (restore = overwrite with wrong text).
- 🟠 **Entity upsert omits `book_id`** — `db_backend.py:186-194,313-321` +
  `db/entities_repo.py:751`: NULL never conflicts, so `import_from_json` doubles the
  global entity set every re-import.
- 🟠 **`delete_chapter` never enables FKs on SQLite** — `db/chapters_repo.py:741-809`
  vs `delete_book` (`db/books_repo.py:598`): orphaned `footnotes` rows on SQLite only.
- 🟠 **`delete_entity`/`change_entity_category`/`get_entity_by_translation` not
  book-scoped** — `db/entities_repo.py:589-673`: deletes/changes hit every book sharing
  the source term (CLI paths `cli.py:1950,2447,1980,2465,3153`).
- 🟠 **Model's self-reported chapter number wins at save** — `ui.py:479-484`: a
  mislabeled raw saves into (and overwrites) the wrong chapter slot; the conflict guard
  checked the right slot. Prefer the user/queue-supplied number.
- 🟠 **`--queue` stores the book title as the queue item's chapter title** —
  `cli.py:548-554` + `:490-496`: on `--resume` it's injected as `{{CHAPTER_TITLE}}`.
- 🟠 **"Merge/append new part" re-translates the whole chapter** —
  `web/services/web_interface.py:305-319`: no common-prefix stripping despite the
  docstring; re-bills and duplicates old text.
- 🟢 **`queue.position` race** — `db/queue_repo.py:100-133`: MAX+1 outside any lock with
  a global UNIQUE; loser's insert silently swallowed in non-strict mode. Single-statement
  `INSERT … SELECT COALESCE(MAX(position),0)+1`.
- 🟢 **`_m002` backfill unguarded on SQLite** — `db/migrations.py:76-92`: copies
  non-numeric `last_chapter` into INTEGER `origin_chapter`.
- 🟢 **`_m001` swallows every DDL exception** — `db/migrations.py:65-73`: hides genuine
  CREATE failures; catch duplicate-index errors specifically.
- 🟢 **`footnotes.strip_footnotes` deletes every `[n]` in prose** — `footnotes.py:22,65-69`:
  bracketed numerics (danmaku counts, citations) silently removed on next save of any
  chapter with footnotes; a prose line starting `"[3] "` at chapter end is misclassified
  as a definition and discarded.

## Correctness under MySQL / dual-backend

> ✅ **Section complete 2026-07-10.** Placeholder rewrite skips quoted regions;
> dedup_entities hard-aborts under DB_BACKEND=mysql; count_comments uses a real
> COUNT(*) (count_comments_admin); get_token_ratio docstring matches the 1.2 default.

- 🟠 **`?`→`%s` rewrite applies inside string literals** — `db_backend.py:32-38`: first
  SQL embedding a literal `?` breaks MySQL-only. Skip quoted regions.
- 🟠 **`dedup_entities.py` hardcodes sqlite3 + `database.db`** — violates the db_backend
  convention; on MySQL deployments it silently "dedups" a stale local file. Port or delete.
- 🟢 **`count_comments` uses `limit=1` + `len(rows)`** — `web/api/comments_admin.py:91-97`:
  returns ≤1 for non-pending statuses.
- 🟢 **`get_token_ratio` docstring says 1.0, returns 1.2** — `db/books_repo.py:663-694`.

## Reliability / async hygiene

> ✅ **Section complete 2026-07-10.** Upload/translate handlers converted to sync
> (threadpool); process_next/start_translation reset is_running on pre-thread
> failure and restore model overrides post-run; per-chapter save mutex closes the
> optimistic-lock TOCTOU (books.py + revisions.py); automod writes guarded with
> only_if_status='pending'; WP publish start/cancel under a lock with book_id
> check; cedict bootstrap lazy + 30s download timeout + LIKE escaping + conn
> try/finally; spaces.exists raises SpacesUnavailable on transport errors
> (prewarm skips instead of rebuilding); CF ban push/remove failures log ERROR;
> /polish re-attaches to the running job; wp_client close()/context-manager +
> orphan-chapter cleanup; reader_stats DNS 3s timeout / 1-day failed-TTL /
> OverflowError→400; sys.path guarded; units.json tmp+replace; logger configures
> handlers once with WARNING file floor; claude_code tmpfile cleanup on stream
> Popen failure; ClaudeProvider only memoizes effort rejection when the 400
> implicates effort params.

- 🔴 **Blocking work in `async def` handlers starves the event loop** —
  `web/api/queue_api.py:132-208,215-297,304-529` (EPUB/FB2 parse, per-chapter queue
  loops, OpenCC), `web/api/books.py:521-548` (PIL + boto3), plus sync DB calls in
  `queue_api.py:597-641`, `translation.py:102-146`,
  `recommendations_public.py:46,66`. A 500-chapter import stalls `/api/health` → the
  watchdog **reboots the VM**. Drop `async` (FastAPI threadpools sync handlers) or
  `asyncio.to_thread`. (Also the strongest argument for the public/admin process split.)
- 🟠 **Stuck busy state** — `translation.py:111-146` + `queue_api.py:619-641`:
  `is_running=True` set before an awaited call that can throw pre-thread-start; every
  later request 409s until restart. try/except reset.
- 🟠 **Sticky global model override** — `translation.py:121-124` + `queue_api.py:560-563`:
  per-request model written into shared `translator.config`, never restored.
- 🟠 **Optimistic-lock TOCTOU** — `web/api/books.py:812-850` + `revisions.py:41-59`:
  read-compare-write instead of compare-and-swap `UPDATE … WHERE translation_date = ?`.
- 🟠 **Automod overwrites concurrent admin moderation** — `web/services/automod.py:136-186`:
  re-read status (or `UPDATE … WHERE status='pending'`) before writing; can flip an
  admin spam-block back to approved and fire notify_reply.
- 🟠 **WP publish race + book-blind cancel** — `web/api/wordpress_api.py:246-271`:
  check-then-act on a global thread handle; `cancel_publish` ignores `book_id`.
- 🟠 **Dictionary bootstrap can hang startup** — `web/api/dictionary_api.py:33-54`:
  `urlretrieve` with no timeout runs synchronously at init (now the ONLY source of
  cedict data since the files were untracked). Add a timeout / defer to first lookup.
- 🟢 **`spaces.exists()` swallows transport errors as False** — `spaces.py:160-169`:
  transient network error → full inline rebuild + re-upload of every book on prewarm.
  Distinguish 404 from transport failure.
- 🟢 **CF ban push/remove failures silent** — `web/services/cf_bans.py:81-103`: admin
  sees "ok" while the edge block never applied. Log loudly.
- 🟢 **`/polish` spawns unbounded daemon threads** — `web/api/grammar.py:413-418`: rapid
  re-clicks stack paid LLM calls. Guard on a running job per chapter.
- 🟢 **New `httpx.Client` per WP request, never closed** — `web/services/wp_client.py:36`;
  `create_chapter` orphans a WP chapter if link-story fails after POST.
- 🟢 **`reader_stats` DNS handling** — `reader_stats_core.py`: `gethostbyaddr` no timeout
  (admin request can hang minutes), failed lookups negative-cached 30 days,
  `parse_duration` OverflowError → 500.
- 🟢 **`sys.path.insert` per upload grows sys.path** — `queue_api.py:222,311`;
  `update_units` truncate-then-write corrupts `units.json` on crash
  (`settings_api.py:193-203`) — use tmp+replace.
- 🟢 **Logger duplicate handlers + ERROR-only floor** — `logger.py:26-46`: second
  instantiation double-logs and re-truncates; warnings (529 waits etc.) never persisted
  unless DEBUG. WARNING floor for the file handler.
- 🟢 **ClaudeCode provider leaks system-prompt tmpfile if Popen raises** —
  `providers/claude_code_provider.py:271-275`.
- 🟢 **ClaudeProvider BadRequest mis-memoizes unrelated errors as effort-rejection** —
  `providers/claude_provider.py:177-192`: check message for "output_config"/"thinking"
  before memoizing.

## Translation pipeline (CLI/engine)

> ✅ **Section complete 2026-07-10.** Retry exhaustion raises a clean "retries
> exhausted" error and `.get('chapter')` tolerates a missing field; EPUB flag
> crossing fixed (--epub-author/--epub-language now work); --retranslate honours
> --format; editor comments use a `#:t9:#` marker so `# ` headings survive;
> _classify_proper_nouns reuses translator.config; get_max_chars reads the factory
> config (no throwaway provider); duplicate entity-save loop deleted; progress bar
> uses the computed width; token-estimate message cites the chunk's char count.

- 🟠 **Retry exhaustion → `parsed_chunk=None` → TypeError** —
  `translation_engine.py:1298,1379,1410-1411`; also bare `['chapter']` KeyError when the
  model omits the field. Fail cleanly with "retries exhausted".
- 🟠 **EPUB export flag crossing** — `cli.py:447-451`: `--epub-author`/`--epub-language`
  ignored; `--book-author` nulls the author. Guards and values are crossed.
- 🟢 **`--retranslate` ignores `--format`** — `cli.py:427-433`: set `output_format`
  before the retranslate dispatch.
- 🟢 **`_edit_chapter_translation` strips legitimate `# ` headings** — `cli.py:1455-1458`.
- 🟢 **`_classify_proper_nouns` builds a fresh TranslationConfig per call** —
  `ui.py:816,849`: ignores `--model` override for the cleaning fallback and re-runs
  dotenv/settings each chapter. Use `self.translator.config`.
- 🟢 **`get_max_chars` constructs a throwaway provider (full HTTP client)** —
  `config.py:160-176`: read it from factory config instead.
- 🟢 **Duplicate entity-save loop with hardcoded categories** — `ui.py:508-529`: superseded
  by the raw-SQL block; delete or derive from book categories.
- 🟢 **Progress bar ignores computed width; token-estimate message cites wrong count** —
  `translation_engine.py:1099,1171,1090`.

## Admin frontend

> ✅ **Section complete 2026-07-10.** handleSave returns success and publish/WP-push
> abort on failure; book-wide Replace All saves the current chapter first; Escape on
> PropagateModal still refreshes the list; RetranslateModal shows CLI-auth providers
> (mirrors Settings' cliAuth); saved API key refetches the provider list; Books
> chapter rows are React.memo + cv-auto (full windowing deferred); proofread tooltip
> keeps the raw timestamp; line-height measurement batched to one reflow; overlay
> scroll is ref+transform (no re-render); failure feedback added on all six listed
> pages; GlobalSearchModal distinguishes errors; duplicates check has loading/error
> + URL rehydration; batch Requeue hidden for originals; EnglishBackdrop renders
> entity AND search highlights; WriteEditor draft-restore failures surface a banner.

- 🟠 **Publish proceeds despite failed save** — `ChapterEditor.jsx` `beforePublish`
  (`:1134`) + `handleWpPublish` (`:643`): `handleSave` swallows errors and returns
  undefined; publish/WP-push then runs with stale content. Return/propagate success
  (WriteEditor already does).
- 🟠 **Book-wide Replace All leaves the current chapter unsaved** —
  `ChapterEditor.jsx:448-490`: server rewrites every other chapter instantly; discard
  leaves the book half-swept. Auto-save current chapter as part of the op.
- 🟠 **Escape during PropagateModal silently skips propagation + list refresh** —
  `EntityFormModal.jsx:250-254,352-360`.
- 🟠 **RetranslateModal filters out CLI-auth providers** —
  `components/editor/RetranslateModal.jsx:19`: `!p.has_key` drops `claudecode`. Mirror
  Settings' `cliAuth = !provider.api_key_env`.
- 🟠 **Settings: saved API key doesn't refresh provider list** — `Settings.jsx:523-533`:
  Test stays disabled / badge stays "No key" until reload.
- 🟠 **Books chapter table unvirtualized (1,754-row books)** — `Books.jsx:429-515`; every
  checkbox toggle re-renders all rows. Memoize rows + window.
- 🟢 **Proofread tooltip shows 1/1/1970** — `ChapterEditor.jsx:69` coerces to bool,
  `:1101` renders it as a date. Keep the raw timestamp.
- 🟢 **Line-height measurement forces one reflow per line** — `ChapterEditor.jsx:303-334`:
  batch append then read.
- 🟢 **Scroll state re-renders whole editor** — `ChapterEditor.jsx:703-726`: ref +
  direct transform instead.
- 🟢 **Failed mutations give no feedback** — `Books.jsx:97-107`, `Queue.jsx:145-148`,
  `Recommendations.jsx:58-73`, `CommentsAdmin.jsx:56-62`, `ApiLogPage.jsx:82-84`,
  `JsonFixPanel.jsx:71-73`.
- 🟢 **GlobalSearchModal renders server errors as "No matches"** —
  `GlobalSearchModal.jsx:44-47`.
- 🟢 **Duplicates check: no loading/error state; URL desync on refresh** —
  `Entities.jsx:289-296`.
- 🟢 **Batch Requeue still offered for original works** — `Books.jsx:398-405` (df44692
  covered the per-row + menu paths only).
- 🟢 **Search mode hides entity highlights entirely** — `EnglishBackdrop.jsx:36-65`:
  render both.
- 🟢 **WriteEditor draft restore silently no-ops on unsupported constructs** —
  `WriteEditor.jsx:419-432`.

## Public reader

> ✅ **Section complete 2026-07-10** (⏳ full TOC windowing, focus traps, "pin the
> bar" setting deferred). Chapter nav goes through react-router (+param→state sync);
> view beacon re-arms on visibilitychange; CommentTree promotes orphans to roots;
> chapter body memoized on [chapter, contentMode, fnIds]; TOC rows cv-auto; h-dvh;
> Library uses ErrorState+retry; comment edit/delete failures show inline errors;
> .markdown-view CSS added; interpolated hover classes replaced with literals;
> arrows gated under entity modal + Escape closes whichever drawer is open;
> ReaderSearch has not-searched/error/empty states; Both mode strips only a leading
> heading (pairing preserved) and hides sentinel-table markers; markFootnoteLine
> skips [n](url) and code spans; reader-progress stores {chapter, scrollRatio}
> (legacy shape tolerated, BookDetail updated); drawers have role=dialog +
> prefers-reduced-motion; book view_count at fetch reviewed and accepted; AZW3
> logs chapter_number=-1 with stats/UI split from EPUB.

- 🔴 **Chapter nav bypasses the router → URL desync** — `Reader.jsx:236` +
  `useUrlState.js:76-109`: raw `replaceState` leaves react-router's pathname stale; any
  drawer open snaps the URL back to the mount-time chapter; refresh/share loads the
  wrong chapter and beats saved progress.
- 🟠 **View beacon drops background tabs permanently** — `Reader.jsx:203-215`: the dwell
  timer checks visibility once and nothing re-arms it on `visibilitychange`.
- 🟠 **CommentTree drops orphaned reply subtrees** — `CommentTree.jsx:5-14,48-56`: an
  edited (→pending) parent hides its approved replies from everyone while the badge
  still counts them. Promote orphans to roots.
- 🟠 **Full markdown parse + DOMPurify on every re-render** — `Reader.jsx:530-574`:
  recomputed on every top-bar hide/show while scrolling. Memoize on
  `[chapter, contentMode, fnIds]`.
- 🟠 **TOC renders 1,800 buttons unvirtualized on every open** — `ReaderTOC.jsx:52-71`:
  at minimum apply the existing `.cv-auto` class; better, window it.
- 🟠 **`h-screen` inner scroller clips bottom nav on iOS Safari** — `Reader.jsx:464`:
  one-token fix, `h-dvh`.
- 🟠 **Library has no error state** — `Library.jsx:123-127,219-225`: API failure renders
  "No books available yet". Use ErrorState + retry.
- 🟠 **Comment edit/delete failures: unhandled rejection, zero feedback** —
  `ReaderComments.jsx:69-91` + `CommentItem.jsx:32-47`.
- 🟢 **`.markdown-view` has no CSS** — comment links invisible-as-links, paragraphs
  collapse (`MarkdownView.jsx:50`; nothing in `index.css`).
- 🟢 **Interpolated Tailwind hover classes never generated for light/sepia** —
  `Reader.jsx:423-455`, `ReaderTOC.jsx:45`, `CommentItem.jsx:85,110,116`, etc.
- 🟢 **Keyboard gaps** — `Reader.jsx:261-278`: arrows flip chapters under the entity
  modal; Escape only closes the search drawer.
- 🟢 **ReaderSearch shows "No matches found" before searching and on errors** —
  `ReaderSearch.jsx:212-216,98-100`.
- 🟢 **"Both" mode line pairing can shift** — `web/api/public.py:253-265` strips
  asymmetrically; sentinel table markers render literal in Both mode.
- 🟢 **`markFootnoteLine` corrupts `[1](url)` links when footnote 1 exists** —
  `chapterMarkdown.js:287`; also matches inside code spans.
- 🟢 **Empty book → misleading "connection dropped" error** — `Reader.jsx:107`.
- 🟢 **No in-chapter scroll persistence** — `reader-progress` stores only the chapter
  number; store `{chapter, scrollRatio}`. Big QoL for 10k-word chapters.
- 🟢 **Drawer a11y** — no `role="dialog"`/focus trap on TOC/Settings/Search/Comments;
  no `prefers-reduced-motion`; a "pin the bar" setting would serve e-ink readers.
- 🟢 **Book `view_count` still bumps at fetch time behind the 5-min cache** —
  `public.py:216`: inconsistent with the dwell-beacon philosophy; move or accept.
- 🟢 **AZW3 downloads logged as `chapter_number=0`** — indistinguishable from EPUB in
  every stats view (`public.py:550`). Use a distinct sentinel.

## Renderer parity (remaining)

> ✅ **Section complete 2026-07-10** (⏳ parity-test harness deferred; engine-level
> CommonMark gaps remain). Raw HTML tokens escaped before python-markdown
> (autolinks preserved) — EPUB/WP now show `<em>x</em>` literally like the Reader;
> linkify converged: markdown-it fuzzy OFF + Python bare-URL linkifier for explicit
> http(s) URLs; 'extra' bundle replaced with tables+fenced_code (no more [^1]/
> def-list divergence); <ol start> allowed by both sanitizers; empty-row fixup
> constrained to sole-row tbody; single-chapter _save_epub uses the shared
> renderer; .md export keeps table runs contiguous, .txt drops sentinel markers;
> bbcode.js [PLAIN]-escapes real XenForo tags in prose + block guard stops
> cross-block sentinel pairs; markdown_notifications skips numeric [n] and only
> reverts its own fingerprint; twkan degrades gracefully without the confusables
> cache; trad_simp strips pre-existing PUA sentinels; decase_lines guards empty
> word; one module-level Markdown instance reused everywhere.

- 🟠 **Raw inline HTML: interpreted by Python, escaped by JS** — `output_formatter.py:170`:
  `<em>x</em>` italic in EPUB/WP, literal in Reader; escape raw-HTML tokens before
  python-markdown.
- 🟠 **linkify: bare URLs are links only in the Reader** — `chapterMarkdown.js:166` vs
  Python side; also fuzzy bare-domain false positives in prose.
- 🟠 **Checked-in cross-renderer parity test** — shared JSON vectors rendered by both
  `_render_markdown` and `renderBlock/renderSegment` (jsdom + DOMPurify), diffed after
  normalization. Every item in this section is a ready seed.
- 🟢 **CommonMark block gaps in python-markdown** — list interrupting a paragraph,
  `1)` lists, `#Heading` (no space; inverted direction), `[^1]` footnote expansion,
  definition lists. Consider disabling the extra-`footnotes`/def-list sub-extensions.
- 🟢 **`<ol start>` stripped by both sanitizers** — add `start` to both allowlists
  (`chapterMarkdown.js:187`, `output_formatter.py:25`).
- 🟢 **Python deletes any all-empty table body row** — `output_formatter.py:175`:
  constrain the header-only fixup.
- 🟢 **Single-chapter `_save_epub` bypasses the markdown renderer** —
  `output_formatter.py:819-827` (legacy CLI path).
- 🟢 **`.md`/`.txt` exports mangle tables/sentinels** — `output_formatter.py:718-722,601-617`.
- 🟢 **bbcode.js: no BBCode escaping of literal `[B]`/`[QUOTE]` in prose; cross-block
  sentinel pairs convert** — `bbcode.js:211-252,21-26`. (Relevant to the "store BBCode
  directly?" discussion.)
- 🟢 **markdown_notifications edge matches** — whole-line `[…]` swallows footnote-like
  lines; `_from_tables` reverses ANY single-column pipe table on disable
  (`modules/markdown_notifications_module.py:48,140-177`).
- 🟢 **twkan skeleton cache is a network fetch at ingest** — `modules/twkan.py:205-214`:
  default-on module; ship `confusables_skeleton.json` in-repo or build at deploy.
- 🟢 **trad_simp PUA sentinel collision** — `trad_simp.py:67-68`: strip/verify U+E000/E001
  in input first.
- 🟢 **`decase_lines` IndexError on empty word** — `chapter_text_ops.py:45`.
- 🟢 **Per-cell `markdown.markdown()` instantiation** — `output_formatter.py:186-188`:
  module-level `Markdown(...)` + `.reset().convert()` (matters at EPUB build over
  chatgroup books).

## Performance / misc

> ✅ **Section complete 2026-07-10** (⏳ `_load_entities()` global-flatten refactor
> and further bundle splitting deferred — editors are already lazy chunks).
> save_entities: one id-map query + executemany; migration 14 adds the
> translation_date index; replace_in_chapters chunks the IN-list at 500;
> _replace_undo bounded to 3 books under a lock; entities_inside_text honours
> do_count; LIKE escaping via ESCAPE '!' (entities API) and '\' (sqlite-only
> dictionary); dictionary lookup closes its connection in finally;
> recommendations_admin uses naive-local time + a status lock against
> double-send.

- 🟢 **`save_entities` N+1 full-cache rewrite** — `db/entities_repo.py:97-166`: 2×N
  round-trips per save across ALL books; batch or scope to the active book. Related:
  global `_load_entities()` flattens all books (`:11-73`) — cross-book collisions can
  persist wrong fields.
- 🟢 **No index on `chapters.translation_date`** — RSS/recent query full-scans
  (`db/chapters_repo.py:392-427`).
- 🟢 **`replace_in_chapters` unchunked IN-list** — `db/chapters_repo.py:626-631`:
  >999 chapters fails on older SQLite; sibling `get_chapters_bulk` chunks at 500.
- 🟢 **`_replace_undo` unbounded + unlocked** — class-level dict holds one full-book
  snapshot per book until restart.
- 🟢 **`entities_inside_text` ignores `do_count`** — `db/entities_repo.py:168-227`:
  regenerating a prompt still advances `last_chapter`.
- 🟢 **LIKE searches don't escape `%`/`_`** — `web/api/entities.py:124-125`,
  `dictionary_api.py:150-154`.
- 🟢 **`dictionary_api.lookup` leaks the sqlite connection on exception** — `:121-195`.
- 🟢 **`recommendations_admin.py:68` uses `utcnow()`** — offset vs the app's naive-local
  convention; concurrent status PUTs can double-send acceptance emails.
- 🟢 **Frontend bundle >500 kB chunk** — vite warning; code-split the editors
  (TipTap/CodeMirror) from the reader path.

## Architecture (discussed, decided directions)

- **Public/admin process split** — `T9_ROLE` router subsets, two systemd units, two
  Apache proxy targets; ebook builds already use cross-process flocks in anticipation.
  Kills the async-starvation class structurally and makes the reader-domain whitelist
  structural.
- **BBCode-native storage for original works** (vs propping Markdown with sentinels) —
  revisit after the parity section above shrinks; scope to original works only if done.
