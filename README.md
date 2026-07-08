# T9 — Web Novel Translation & Publishing Platform

T9 started as an AI-powered tool for translating web novels into English and has grown into a full publishing platform. It has two halves that matter about equally:

- **A translation & writing studio** — multi-provider AI translation (OpenAI, Claude, Gemini, DeepSeek, OpenRouter, or your local Claude Code subscription) with an entity glossary that keeps terminology consistent across thousands of chapters, a split-pane proofreading editor, a WYSIWYG editor for original fiction, and batch queue processing.
- **A public reader site** — a clean, login-free Library and Reader for your finished work, with chapter scheduling and drip releases, anonymous comments with moderation, RSS feeds, EPUB downloads, reader statistics, and CDN-backed covers and illustrations.

Source languages include Chinese, Japanese, Korean, and Russian, with genre-specific prompt presets (xianxia, light novel, Korean web novel, and more).

## Feature Highlights

### Translation engine
- **Multi-provider AI translation** — OpenAI, Anthropic Claude, Google Gemini, DeepSeek, OpenRouter, and a `claudecode` provider that drives the local Claude Code CLI (translate on your Claude subscription with no API key; the queue pauses and resumes automatically around session limits)
- **Entity consistency** — automatically identifies proper nouns and maintains a per-book glossary so names, places, and terms stay consistent across hundreds of chapters
- **Entity review** — after each chapter, review newly discovered entities, fix translations, and delete false positives before they propagate; retroactive review revisits earlier entities with AI advice, dictionary lookup, and propagation to affected chapters
- **Queue processing** — upload EPUB, FB2, or text files and translate back-to-back with auto-processing; resilient to provider overload (529 responses trigger a patient retry loop instead of burning the retry budget)
- **Streaming output** — real-time translation progress with chunk-by-chunk status over WebSocket
- **Genre presets** — pre-built prompts per genre, copied per book at creation so you can customize each book's system prompt independently

### Books, proofreading & rich content
- **Chapter editor** — split-pane view with source on the left, editable English on the right, entity highlighting, CC-CEDICT dictionary lookup, and selection-based LLM retranslation with ruby-text comparison
- **Search & replace** — chapter-level or book-wide, regex support, cross-chapter navigation, one-click undo for bulk replacements, and a global search modal on the Books page
- **Markdown rich text** — chapters support block-level Markdown (headings, blockquotes, tables, and more) rendered identically in the Reader, EPUB, HTML export, and WordPress
- **Illustrations** — in-chapter images via `⟦IMG:id⟧` markers that survive the translate → edit → export pipeline and render inline everywhere
- **Footnotes** — persistent per-book footnotes (cultural notes, incantation glosses) that re-anchor automatically on every chapter save

### Original works & the Write editor
- **WYSIWYG Write editor** (TipTap) for fiction written directly in the browser — original books get it as their default editor, and translation books can use it too
- **Round-trip safety** — content is stored as Markdown; every save is serialize → reparse → compare guarded, so the editor can never silently rewrite your text
- **Autosave & revisions** — server autosave with optimistic locking (conflict banner instead of silent clobber), plus manual/auto revision snapshots with one-click restore
- **Writing tools** — live word count, session counter, daily goal, focus/typewriter mode, Reader-parity preview
- **Grammar & polish** — local LanguageTool integration plus an LLM polish pass, with your entity dictionary suppressing false positives on invented names
- **Rich formatting** — XenForo-parity tables (multi-paragraph cells, lists in cells), underline and text color, with BBCode export for forum posting

### Publishing
- **Drafts & scheduling** — every chapter is draft, scheduled, or live (`published_at` timestamp); scheduled chapters go live automatically with no cron
- **Drip releases** — batch-publish a run of chapters with a stagger interval (e.g. one chapter every 12 hours)
- **EPUB export** — per-book EPUB generation with covers and illustrations; the public download is published-chapters-only and regenerates itself when a scheduled chapter crosses its publish time
- **WordPress / Fictioneer** — publish books to a WordPress site running the Fictioneer theme, incrementally (unchanged chapters skipped)
- **RSS** — a site-wide feed of recent chapters plus per-book feeds, with autodiscovery tags on reader pages

### The public reader site
- **Library** — public book listing with covers, tags, status, and view counts; no login required for readers
- **Reader** — chapter navigation, table of contents, full-text search, keyboard and swipe navigation, translated/source/interleaved display modes, light/sepia/dark themes, adjustable font size
- **Comments** — anonymous per-chapter comments protected by Cloudflare Turnstile, with optional email reply notifications (and one-click unsubscribe), an admin moderation queue, optional AI auto-moderation, and IP bans that push to the Cloudflare edge
- **Recommendations** — a public "recommend me something" form (also Turnstile-protected) feeding an admin review page
- **Reader statistics** — view counts and per-chapter reading stats in the admin UI

### Per-book modules
Composable text-transform modules, toggled per book (auto by source URL, or forced on/off), each with its own settings:

| Module | What it does |
|---|---|
| Traditional → Simplified | OpenCC conversion at ingest so Taiwan-sourced raws match your entity glossary |
| Chatgroup Transformer | Normalizes "group chat" novels — wraps 叮！/Ding notifications and username:message lines (entity-verified) into 【…】 blocks, on both source and translated text |
| Markdown Notifications | Renders 【…】 system/notification blocks as boxed tables in the Reader |
| Chapter Spacing | Normalizes paragraph spacing |
| Unit Converter | Converts Chinese/metric units in translations, with annotate or replace modes and LLM false-positive filtering |
| Partial Repair | Re-translates lines that still contain source-language characters after the main pass |
| novel543 / twkan | Site-specific boilerplate strippers for scraped sources |

Enabling, disabling, or reconfiguring a module backfills (or reverses) the transformation across the whole book — as a background task with progress in the UI and guardrails so conflicting operations can't run concurrently. Module activity is summarized to the activity log without per-chapter spam.

### Infrastructure
- **Dual database backend** — SQLite by default, MySQL (with connection pooling) via `DB_BACKEND=mysql`; schema migrations run automatically
- **Object storage / CDN** — covers, illustrations, and EPUBs can be offloaded to S3-compatible storage (DigitalOcean Spaces) and served from CDN URLs
- **Activity log & cost tracking** — persistent activity feed plus a per-call API log with token counts
- **Operations** — systemd service, watchdog, health endpoint, reverse-proxy configs for Apache/Nginx, and a pytest suite covering the DB layer, HTTP API, and text pipelines

## Quick Start

### Prerequisites

- Python 3.10+
- Node.js 18+ and npm
- At least one AI provider API key (or a Claude Code login for the `claudecode` provider)

### Installation

```bash
git clone https://github.com/SpiraMirabilis/translate
cd translate

# Python dependencies
pip install -r requirements.txt
pip install -r web/requirements_web.txt

# Frontend dependencies
cd web/frontend && npm install && cd ../..
```

### API Keys

Create a `.env` file in the project root:

```env
# Add the providers you want to use
OPENAI_KEY=sk-...
ANTHROPIC_KEY=sk-ant-...
GOOGLE_AI_KEY=AIza...
DEEPSEEK_KEY=sk-...
OPENROUTER_KEY=sk-or-...

# Optional: set default models
TRANSLATION_MODEL=claude:claude-sonnet-4-6
ADVICE_MODEL=oai:gpt-5-mini
```

API keys can also be set through the web GUI on the Settings page. Non-secret settings (models, site branding, feature toggles) live in `settings.json`, managed from the Settings page — `.env` is only for secrets and infrastructure.

### Launch

```bash
./start_web.sh
```

This starts both the backend API server and the frontend dev server. Open **http://localhost:5173** in your browser.

To stop, press `Ctrl+C` — both servers shut down together.

## Getting Started

Once T9 is running, here's how to translate your first book.

### 1. Create a book

Go to **Books** and click **New Book**. Enter a title and author, then pick a genre preset. The genre determines the source language and loads an optimized system prompt — for example, "Chinese Xianxia" includes instructions for cultivation terminology, while "Japanese Light Novel" handles honorifics and Japanese narrative conventions. You can also choose "Custom" and write your own prompt, or check **Original work** to write fiction directly in the browser instead of translating.

### 2. Upload your chapters

Go to **Queue** and upload your source material:

- **EPUB / FB2** — upload the file and assign it to your book. T9 extracts each chapter automatically and adds them to the queue with sequential chapter numbers (FB2/FB2.zip covers Russian sources).
- **Text files** — upload individual `.txt` files, many at once. Chapter numbers are auto-detected from filenames when possible.
- **Paste** — for a single chapter, go to the **Translate** page, paste the source text, and translate it there.

### 3. Start translating

On the Queue page, select your translation model (and optionally an advice model for entity suggestions and a cleaning model for filtering false-positive entities). Click **Process Next** to translate one chapter, or enable **Auto-process** to translate them back-to-back. Check **Save as drafts** if you want to schedule the releases yourself instead of publishing immediately.

### 4. Review entities

When the translator finds new proper nouns — character names, places, organizations — it pauses and shows you a review panel. Check each translation, fix mistakes, delete any common words that were misidentified as entities, and click **Submit**. Translation resumes with the corrected glossary, and those terms stay consistent for every future chapter.

### 5. Proofread

After a chapter finishes, go to **Books**, expand the book, and click **Edit** on the chapter. The split-pane editor shows the source text on the left and the editable English translation on the right. You can proofread and fix the translation while the next chapter processes in the background. Mark it proofread when you're satisfied.

### 6. Publish

Chapters from the translation pipeline go live immediately by default; original-work chapters are born as drafts. Use the publish chip in either editor (or the batch Publish action on Books) to publish now, schedule for later, or set up a drip release. Then share your public library URL, export an EPUB, or push to WordPress.

## Supported Providers

| Provider | Alias | Example Models | API Key Env |
|----------|-------|----------------|-------------|
| OpenAI | `oai` | gpt-5.4, gpt-5-mini, o3-mini | `OPENAI_KEY` |
| Anthropic | `claude` | claude-sonnet-4-6, claude-opus-4-6, claude-haiku-4-5 | `ANTHROPIC_KEY` |
| Google Gemini | `gemini` | gemini-2.5-flash, gemini-2.5-pro | `GOOGLE_AI_KEY` |
| DeepSeek | `ds` | deepseek-chat | `DEEPSEEK_KEY` |
| OpenRouter | `or` | any model on OpenRouter | `OPENROUTER_KEY` |
| Claude Code | `cc` | sonnet, opus | *(none — uses your local Claude Code login)* |

Model format: `provider:model-name` (e.g. `claude:claude-sonnet-4-6`, `cc:sonnet`)

The `cc` provider shells out to the Claude Code CLI, so translation costs come out of a Claude subscription instead of API billing. When a session usage limit is hit mid-queue, T9 parks the job and resumes automatically when the limit resets.

Providers can be configured and new models added by editing `providers/models.json`.

## Web GUI Pages

### Translate (Dashboard)

The main workspace for single-chapter translation. Paste source text, select a book and chapter, choose your models, and hit Translate. The right panel streams the translation output in real time and shows a status log. Options include entity review, entity cleaning, partial repair, unit conversion, and save-as-draft.

### Books

Book and chapter management hub. Create books with genre presets, upload cover images, set custom system prompts, manage entity categories, and configure per-book modules. Chapters show draft/scheduled/published state and can be edited, marked proofread, deleted, requeued for retranslation, or batch-published with a drip schedule. Global cross-chapter search (`Ctrl+F`) jumps directly into the editor. Export EPUBs or publish to WordPress from here.

### Chapter Editor

Split-pane proofreading view with source text on the left (read-only) and editable English translation on the right: entity highlighting with inline editing, CC-CEDICT dictionary lookup, selection-based LLM retranslation with ruby-text comparison, chapter- and book-wide search & replace with regex and undo, and the publish chip.

### Write Editor

WYSIWYG editor for original fiction (and any translation chapter you open it on). Formatting toolbar and bubble menu, tables, underline/color, inline illustrations, footnotes, live word count and daily goal, focus/typewriter mode, grammar checking with LLM polish, revision history slide-over, and autosave with conflict protection. Storage stays Markdown under the hood, and a round-trip guard blocks any save the serializer can't reproduce exactly.

### Entities

Browse, search, edit, and manage the entity glossary. Per-entity categories, gender tracking, translation notes (included in AI prompts), AI advice from a secondary model, dictionary lookup, duplicate detection, and propagation — change an entity's translation and find-and-replace it across all chapters or requeue affected chapters.

### Queue

Batch processing for translating many chapters in sequence. Upload `.txt`, EPUB, or FB2 files, assign them to books, and process back-to-back. Auto-process mode translates continuously, pausing only for entity review.

### Comments

Moderation queue for reader comments: approve, delete, reply as admin, ban IPs (optionally pushed to the Cloudflare edge), and review what the AI auto-moderator flagged.

### Recommendations

Reader-submitted recommendations from the public form, with admin review.

### Stats & Logs

Reader statistics (views per book/chapter), the API call log with per-call token counts and costs, and the persistent activity log.

### Settings

Configure provider API keys (with test buttons), default models, site branding for the public reader, the public library toggle, unit conversion rules, traditional→simplified conversion, grammar checking, comment auto-moderation, and WordPress credentials. Non-secret values persist to `settings.json`; secrets go to `.env`.

### Reader & Library

The same Reader and Library that the public sees, accessible from the admin UI. When the public library is enabled, unauthenticated visitors can browse published books, read published chapters, comment, subscribe to RSS, and download EPUBs — with drafts and scheduled chapters invisible until they go live.

## The Public Site

Everything a reader touches is gated on publication state at query time:

- **Library** (`/library`) lists public books with published-chapter counts and last-release dates.
- **Reader** serves only published chapters; scheduled chapters appear the moment their time arrives — no cron job, no restart.
- **EPUB download** contains published chapters only and regenerates automatically when a scheduled chapter crosses its publish time.
- **RSS** — `/api/public/feed.rss` (site-wide) and per-book feeds with autodiscovery `<link>` tags on reader pages.
- **Comments** are anonymous with Turnstile verification; commenters can opt into email notifications for replies (sent via local Postfix, with unsubscribe links). Comments on unpublished chapters 404.
- **Covers and illustrations** are served from CDN URLs when Spaces offload is enabled.

A standalone Apache config (`deploy/apache2-reader.conf`) exposes only the public surface if you want the admin UI reachable on a separate host or VPN.

## WordPress / Fictioneer Publishing

T9 can publish translated books directly to a WordPress site running the [Fictioneer](https://github.com/Tetrakern/fictioneer) theme. A small companion plugin (`deploy/fictioneer-rest-meta.php`) handles Fictioneer-specific metadata like chapter-to-story linking, word counts, and story ordering.

Setup:

1. **Install the plugin** on your WordPress site — copy `deploy/fictioneer-rest-meta.php` to `wp-content/plugins/fictioneer-rest-meta/` and activate it, or run `bash deploy/install-wp-plugin.sh /path/to/wordpress`.
2. **Create an Application Password** in WordPress (Users > Profile > Application Passwords).
3. **Configure T9** — go to Settings and fill in your WordPress URL, username, and the application password. Click Test Connection to verify.
4. **Publish** — go to Books, click the globe icon on a book, set the story status and rating, and click Publish All.

Re-publishing is safe and incremental — unchanged chapters are skipped, modified chapters are updated, and new chapters are created. See the Help page in the web GUI for detailed setup instructions and troubleshooting.

## Project Structure

```
t9/
├── start_web.sh           # Launch script (backend + frontend dev server)
├── run_web.py             # Alternative launcher (Python, no Vite)
├── translator.py          # CLI entry point
├── translation_engine.py  # Core translation logic
├── database.py            # Compatibility shim → db/ package
├── db/                    # Database layer: repos (books, chapters, entities,
│                          #   queue, footnotes, revisions, ...), migrations
├── db_backend.py          # SQLite/MySQL backend abstraction
├── modules/               # Per-book transform modules + background task runner
├── providers/             # AI provider modules + models.json
├── prompts/               # System prompt templates per genre
├── genres.json / genres.py
├── epub_processor.py      # EPUB import
├── fb2_processor.py       # FB2 (FictionBook) import
├── output_formatter.py    # Text/HTML/Markdown/EPUB rendering
├── illustrations.py       # ⟦IMG⟧ marker pipeline
├── footnotes.py           # Footnote persistence & re-anchoring
├── spaces.py              # S3/Spaces (CDN) offload
├── settings_store.py      # settings.json (non-secret runtime settings)
├── web/                   # Web GUI + public site
│   ├── app.py             # FastAPI application
│   ├── auth.py            # Session-based authentication
│   ├── cedict.db          # CC-CEDICT Chinese dictionary
│   ├── api/               # REST + WebSocket endpoints (admin + public)
│   ├── services/          # Job manager, exporters, view logger, ...
│   └── frontend/          # React + Vite + Tailwind CSS
│       └── src/pages/     # Dashboard, Books, ChapterEditor, WriteEditor,
│                          # Entities, Queue, CommentsAdmin, Recommendations,
│                          # ReaderStats, ApiCalls, Settings, Help,
│                          # Reader, Library, Login
├── tests/                 # pytest suite (DB layer, HTTP API, text pipelines)
├── deploy/                # systemd units, watchdog, reverse-proxy configs,
│                          # WordPress plugin
├── requirements.txt
└── database.db            # SQLite database (created on first run)
```

## CLI

T9 also has a full command-line interface that supports all operations — translation, book/chapter management, entity review, queue processing, and more. See **[CLIReadme.md](CLIReadme.md)** for complete documentation.

## Authentication

T9 includes built-in password authentication. **If you expose this app to the internet — on a VM, VPS, or through a tunnel — you must enable authentication.** Without it, anyone who finds your URL has full access to your API keys, translation data, and database.

To enable it, add `T9_PASSWORD` to your `.env`:

```env
T9_PASSWORD=your-secure-password-here
```

When set, all admin API endpoints and WebSocket connections require a valid session. The public reader endpoints are separate and intentionally unauthenticated — they only ever serve published content from public books, and only when the public library is enabled.

When `T9_PASSWORD` is not set, authentication is disabled entirely — appropriate for local-only use on `127.0.0.1`.

If serving over HTTPS (which you should be, if network-exposed), also set `T9_SECURE_COOKIE=true` in `.env` so the session cookie is only sent over encrypted connections.

Your `.env` file contains API keys and your app password. Lock down its permissions:

```bash
chmod 600 .env
```

## Deployment

The `deploy/` directory contains ready-to-use configuration files for running T9 on a server.

### systemd service

A `systemd --user` service that manages the app, with a pre-flight check that refuses to start if `T9_PASSWORD` is not set in `.env`.

```bash
# Build the frontend for production (no Vite dev server needed)
cd web/frontend && npm run build && cd ../..

# Install the service
mkdir -p ~/.config/systemd/user
cp deploy/t9.service ~/.config/systemd/user/

# Edit paths in the service file if your install location differs

systemctl --user daemon-reload
systemctl --user start t9
systemctl --user status t9

# Enable on boot
loginctl enable-linger $USER
systemctl --user enable t9

# View logs
journalctl --user -u t9 -f
```

**Note on lingering:** `systemctl --user` services normally live and die with your login session — without lingering enabled, T9 will stop when you log out and won't start on boot. `loginctl enable-linger $USER` fixes that, but on many distros it requires root (or polkit authorization), so you may need `sudo loginctl enable-linger $USER`. Verify with `loginctl show-user $USER --property=Linger` — it should say `Linger=yes`.

### Watchdog service

An optional watchdog (`deploy/t9-watchdog.service` + `deploy/t9_watchdog.py`) monitors the T9 health endpoint and recovers the service if it becomes unresponsive.

### LanguageTool grammar server (optional)

The write editor's grammar check proxies to a local [LanguageTool](https://languagetool.org/) HTTP server. `bash deploy/setup-languagetool.sh` downloads the release, writes the server config, and installs it as a `systemctl --user` service on `127.0.0.1:8081`; add `--with-ngrams` to also fetch the ~8 GB English ngram data that powers confusion-pair rules (their/there, its/it's). The script is heavily commented for adapting to non-Debian or non-systemd systems. T9 runs fine without it — grammar checking just stays unavailable.

### Reverse proxy

Sample configurations for Apache2 and Nginx are in `deploy/`. Both include HTTPS redirection, WebSocket proxying (important for real-time translation progress and entity review), and appropriate timeouts for long-running WebSocket connections.

- **`deploy/apache2-reverse-proxy.conf`** — requires `mod_proxy`, `mod_proxy_http`, `mod_proxy_wstunnel`, `mod_ssl`, `mod_rewrite`
- **`deploy/nginx-reverse-proxy.conf`** — includes a 1-hour `proxy_read_timeout` on `/ws` so the connection survives long entity review waits
- **`deploy/apache2-reader.conf`** — standalone config for exposing only the public library/reader

Replace `t9.example.com` with your actual domain. Both examples assume Let's Encrypt certificates via certbot.

### MySQL (optional)

SQLite is the default and needs no setup. For a heavier deployment, set `DB_BACKEND=mysql` plus the `MYSQL_*` variables — the schema is created and migrated automatically, and the same code paths run against either backend.

## Environment Variables

Secrets and infrastructure go in `.env`; most other settings are managed from the Settings page (persisted to `settings.json`).

| Variable | Description |
|----------|-------------|
| `OPENAI_KEY` / `ANTHROPIC_KEY` / `GOOGLE_AI_KEY` / `DEEPSEEK_KEY` / `OPENROUTER_KEY` | Provider API keys |
| `TRANSLATION_MODEL` | Default translation model (e.g. `claude:claude-sonnet-4-6`) |
| `ADVICE_MODEL` | Default entity advice model |
| `T9_PASSWORD` | Enable authentication (required if exposed to the internet) |
| `T9_SECURE_COOKIE` | Set to `true` when serving over HTTPS |
| `T9_PUBLIC_LIBRARY` | Enable the public library/reader |
| `SITE_NAME` / `PUBLIC_SITE_NAME` | Branding for the admin UI / public site |
| `SITE_BASE_URL` | Public base URL — used for RSS links and email links |
| `DB_BACKEND` | `sqlite` (default) or `mysql` |
| `MYSQL_HOST` / `MYSQL_PORT` / `MYSQL_USER` / `MYSQL_PASS` / `MYSQL_DB` / `MYSQL_POOL_SIZE` | MySQL connection settings |
| `SPACES_ENABLED` / `SPACES_BUCKET` / `SPACES_REGION` / `SPACES_PREFIX` / `SPACES_CDN_BASE` | S3-compatible object storage (DigitalOcean Spaces) offload |
| `BUCKET_ACCESS_ID` / `BUCKET_SECRET` / `BUCKET_ENDPOINT` | Object storage credentials |
| `CF_TURNSTILE_SITE_KEY` / `CF_TURNSTILE_SECRET_KEY` | Cloudflare Turnstile for comments & recommendations |
| `CF_API_EMAIL` / `CF_API_KEY` | Cloudflare credentials for pushing comment IP bans to the edge |
| `COMMENT_AUTOMOD_ENABLED` / `COMMENT_AUTOMOD_MODEL` | AI auto-moderation of new comments |
| `EMAIL_FROM` | Sender address for comment reply notifications (local Postfix) |
| `TRAD_TO_SIMP` | Global default for traditional→simplified conversion at ingest |
| `GRAMMAR_CHECK_ENABLED` / `LANGUAGETOOL_URL` / `GRAMMAR_LANGUAGE` / `POLISH_MODEL` | Write-editor grammar checking & LLM polish |
| `WP_URL` / `WP_USERNAME` / `WP_APP_PASSWORD` | WordPress publishing credentials |
| `OVERLOAD_RETRY_WAIT_SECONDS` | Wait between retries when a provider returns 529 Overloaded (default 300) |
| `DEBUG` | Enable debug logging (`True`/`False`) |

## License

MIT
