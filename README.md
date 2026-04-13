# T9 — Web Novel Translator

T9 is an AI-powered tool for translating web novels into English. It supports Chinese, Japanese, and Korean source languages using large language models (OpenAI, Claude, Gemini, DeepSeek, OpenRouter) and maintains consistent terminology across an entire book through an entity management system that tracks character names, places, organizations, and other proper nouns.

## Features

- **Multi-provider AI translation** — OpenAI, Anthropic Claude, Google Gemini, DeepSeek, and OpenRouter, with easy extensibility for new providers
- **Multi-language support** — Chinese, Japanese, and Korean with genre-specific prompt presets (xianxia, light novel, Korean web novel, etc.)
- **Entity consistency** — automatically identifies proper nouns and maintains a per-book glossary so names, places, and terms stay consistent across hundreds of chapters
- **Entity review** — after each chapter, review newly discovered entities, fix translations, and delete false positives before they propagate
- **Retroactive entity review** — go back and review entities introduced in earlier chapters, with AI advice, dictionary lookup, and propagation options to update all affected chapters
- **Entity cleaning** — optional second-pass with a lightweight model to filter out common words misidentified as entities
- **Queue processing** — upload many chapters and translate them back-to-back with auto-processing
- **Book management** — organize translations into books with per-book custom system prompts, entity categories, chapter tracking, cover images, and EPUB export
- **Genre presets** — pre-built translation prompts optimized for specific genres (Chinese xianxia, Japanese light novel, Korean web novel), with custom categories and terminology guidance
- **Chapter editor** — split-pane proofreading view with source on the left, editable English on the right, entity highlighting, dictionary lookup, and LLM retranslation with ruby text comparison
- **Search & replace** — find text across a single chapter or an entire book, with regex support, cross-chapter navigation, and one-click undo for bulk replacements
- **Unit conversion** — automatically converts Chinese/metric units in translated text, with configurable annotation or replacement modes
- **Partial repair** — auto-retranslates lines that still contain untranslated source characters after the main translation pass
- **WordPress publishing** — publish translated books directly to a WordPress site running the Fictioneer theme, with incremental updates
- **Public library** — optional public-facing reader with chapter navigation, search, bookmarks, and theme support (light/sepia/dark) — no login required for readers
- **Streaming output** — real-time translation progress with chunk-by-chunk status updates via WebSocket
- **Two interfaces** — web GUI (recommended) and full-featured CLI

## Quick Start

### Prerequisites

- Python 3.10+
- Node.js 18+ and npm
- At least one AI provider API key

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

API keys can also be set through the web GUI on the Settings page.

### Launch

```bash
./start_web.sh
```

This starts both the backend API server and the frontend dev server. Open **http://localhost:5173** in your browser.

To stop, press `Ctrl+C` — both servers shut down together.

## Getting Started

Once T9 is running, here's how to translate your first book.

### 1. Create a book

Go to **Books** and click **New Book**. Enter a title and author, then pick a genre preset. The genre determines the source language and loads an optimized system prompt — for example, "Chinese Xianxia" includes instructions for cultivation terminology, while "Japanese Light Novel" handles honorifics and Japanese narrative conventions. You can also choose "Custom" and write your own prompt later.

### 2. Upload your chapters

Go to **Queue** and upload your source material:

- **EPUB** — click "Upload EPUB", select the file, and assign it to your book. T9 extracts each chapter automatically and adds them to the queue with sequential chapter numbers.
- **Text files** — click "Upload File" to add individual `.txt` files. You can upload many at once. Set the book and starting chapter number. Chapter numbers are auto-detected from filenames when possible.
- **Paste** — for a single chapter, you can also go to the **Translate** page, paste the source text directly, and translate it there.

### 3. Start translating

On the Queue page, select your translation model (and optionally an advice model for entity suggestions and a cleaning model for filtering false-positive entities). Click **Process Next** to translate one chapter, or enable **Auto-process** to translate them back-to-back.

### 4. Review entities

When the translator finds new proper nouns — character names, places, organizations — it pauses and shows you a review panel. Check each translation, fix mistakes, delete any common words that were misidentified as entities, and click **Submit**. Translation resumes with the corrected glossary, and those terms stay consistent for every future chapter.

### 5. Proofread

After a chapter finishes, go to **Books**, expand the book, and click **Edit** on the chapter. The split-pane editor shows the source text on the left and the editable English translation on the right. You can proofread and fix the translation while the next chapter processes in the background. Mark it proofread when you're satisfied.

### 6. Export or publish

Once all chapters are translated and proofread, click **Export EPUB** on the Books page to generate an ebook. You can also publish directly to WordPress if you have the Fictioneer integration set up (see below), or share via the public library.

## Supported Providers

| Provider | Alias | Example Models | API Key Env |
|----------|-------|----------------|-------------|
| OpenAI | `oai` | gpt-5.4, gpt-5-mini, o3-mini | `OPENAI_KEY` |
| Anthropic | `claude` | claude-sonnet-4-6, claude-opus-4-6, claude-haiku-4-5 | `ANTHROPIC_KEY` |
| Google Gemini | `gemini` | gemini-2.5-flash, gemini-2.5-pro | `GOOGLE_AI_KEY` |
| DeepSeek | `ds` | deepseek-chat | `DEEPSEEK_KEY` |
| OpenRouter | `or` | any model on OpenRouter | `OPENROUTER_KEY` |

Model format: `provider:model-name` (e.g. `claude:claude-sonnet-4-6`, `gemini:gemini-2.5-flash`)

Providers can be configured and new models added by editing `providers/models.json`.

## Web GUI Pages

### Translate (Dashboard)

The main workspace for single-chapter translation. Paste source text, select a book and chapter, choose your models, and hit Translate. The right panel streams the translation output in real time and shows a status log. Options include entity review, entity cleaning, partial repair, and unit conversion toggles.

### Books

Book and chapter management hub. Create books with genre presets, upload cover images, set custom system prompts, and manage entity categories. Chapters can be individually edited, marked as proofread, deleted, or requeued for retranslation. Batch operations let you select multiple chapters at once. Global cross-chapter search (`Ctrl+F`) finds text across all chapters and jumps directly into the editor. Export entire books as EPUB.

### Chapter Editor

Split-pane proofreading view with source text on the left (read-only) and editable English translation on the right. Tools include:

- **Entity highlighting** — toggle to highlight known entities in both panels with category-specific colors. Click to edit inline.
- **Dictionary lookup** — select text to look it up in CC-CEDICT (Chinese dictionary).
- **LLM retranslation** — select a source passage and request an AI retranslation. The result appears as ruby text above the original for comparison.
- **Search & replace** — chapter-level or book-wide, with regex support. Book-wide Replace All has a one-click undo.

### Entities

Browse, search, edit, and manage the entity glossary. Features include per-entity categories, gender tracking for characters, translation notes (included in AI prompts), AI advice from a secondary model, dictionary lookup, duplicate detection, and propagation — when you change an entity's translation, you can find-and-replace the old translation across all chapters or requeue affected chapters. Retroactive review lets you revisit entities from earlier chapters with full context.

### Queue

Batch processing for translating many chapters in sequence. Upload `.txt` files or EPUBs, assign them to books, and process them back-to-back. Auto-process mode translates continuously, pausing only for entity review. Supports all the same model and option controls as the Dashboard.

### Settings

Configure API provider keys (with test buttons to verify), set default translation and advice models, toggle debug mode, manage unit conversion rules (configurable JSON for which units to convert and whether to annotate or replace), enable the public library, export entity data as JSON, and configure WordPress/Fictioneer publishing credentials.

### Reader

A clean reading interface for translated books. Features chapter navigation, table of contents, full-text search (`Ctrl+F`), swipe gestures for mobile, keyboard navigation (arrow keys), and three display modes: translated text only, source text only, or both interleaved. New entities introduced in each chapter are shown as color-coded badges. Supports light, sepia, and dark themes with customizable font size.

### Library

Optional public-facing book listing. When enabled in Settings, unauthenticated visitors can browse your translated books, read chapters, and download EPUBs — without needing to log in or having access to the translation tools.

### Help

Built-in guide covering the recommended workflow, feature reference for each page, and WordPress setup instructions.

## Search & Replace

T9 has built-in search and replace for proofreading and consistency fixes.

### Chapter Editor search (`Ctrl+F` / `Ctrl+H`)

- **Scope** — search Translated text, Source text, or Both
- **Regex** — toggle the `.*` button for regular expression matching
- **Book-wide** — click the book icon to search across all chapters. `Enter` and `Shift+Enter` navigate matches across chapter boundaries automatically
- **Replace** — replace the current match or Replace All (translated text only — source is read-only). Book-wide Replace All modifies every chapter in one operation
- **Undo** — after a book-wide Replace All, an undo toast appears for 15 seconds to revert all changes

### Global search (Books page, `Ctrl+F`)

Click the search icon on the Books page to open a modal that searches across all chapters of a book. Results are grouped by chapter with match counts. Clicking a result opens the Chapter Editor with the search pre-loaded and positioned on the first match.

## Unit Conversion

T9 can automatically convert Chinese units (li, jin, zhang, etc.) to metric equivalents in the translated text. This runs as a two-step post-processing pass after translation:

1. **Regex matching** — a regular expression scans the translated text for unit patterns (e.g. "three hundred li", "fifty jin").
2. **False-positive filtering** — all matches are sent to the cleaning model, which evaluates each one in context and removes false positives (e.g. idiomatic phrases that aren't actual measurements). If no cleaning model is set, this step is skipped and all regex matches are converted directly.

Configuration is done on the Settings page via an editable JSON block. Each unit entry specifies the conversion factor and an action:

- **annotate** — keeps the original text and adds a parenthetical, e.g. "thirty li (15 km)"
- **replace** — substitutes the converted value directly, e.g. "15 kilometers"

Unit conversion can be toggled on or off per translation from the Dashboard or Queue page.

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
├── database.py            # SQLite database manager
├── config.py              # Configuration
├── cli.py                 # Command-line interface
├── ui.py                  # Abstract UI base class
├── unit_converter.py      # Post-translation unit conversion
├── genres.json            # Genre preset definitions
├── genres.py              # Genre loading utilities
├── prompts/               # System prompt templates per genre
│   ├── chinese_xianxia.txt
│   ├── japanese_light_novel.txt
│   └── korean_web_novel.txt
├── providers/             # AI provider modules
│   ├── factory.py
│   ├── models.json        # Provider and model configuration
│   ├── openai_provider.py
│   ├── claude_provider.py
│   └── gemini_provider.py
├── web/                   # Web GUI
│   ├── app.py             # FastAPI application
│   ├── auth.py            # Session-based authentication
│   ├── cedict.db          # CC-CEDICT Chinese dictionary
│   ├── api/               # REST + WebSocket endpoints
│   ├── services/          # Job manager, web interface
│   └── frontend/          # React + Vite + Tailwind CSS
│       └── src/
│           ├── pages/     # Dashboard, Books, ChapterEditor, Entities,
│           │              # Queue, Settings, Help, Reader, Library,
│           │              # BookDetail, Login
│           ├── components/
│           ├── hooks/
│           ├── services/
│           └── utils/
├── deploy/                # Deployment configs
│   ├── t9.service         # systemd --user service
│   ├── t9-watchdog.service # Watchdog auto-restart service
│   ├── t9_watchdog.py     # Watchdog monitor script
│   ├── nginx-reverse-proxy.conf
│   ├── apache2-reverse-proxy.conf
│   ├── apache2-reader.conf # Standalone reader config
│   ├── fictioneer-rest-meta.php  # WordPress plugin
│   └── install-wp-plugin.sh
├── requirements.txt       # Python dependencies (core + CLI)
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

When set, all API endpoints and WebSocket connections require a valid session. Users see a login page and must enter the password to access the app. Sessions last 30 days via a signed cookie, so you won't need to re-authenticate often.

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

# Edit paths in the service file if your install location differs from ~/Documents/code/t9

systemctl --user daemon-reload
systemctl --user start t9
systemctl --user status t9

# Enable on boot (requires lingering so it runs without an active login session)
loginctl enable-linger $USER
systemctl --user enable t9

# View logs
journalctl --user -u t9 -f
```

### Watchdog service

An optional watchdog (`deploy/t9-watchdog.service` + `deploy/t9_watchdog.py`) monitors the T9 process and automatically restarts it if it becomes unresponsive. Install it alongside the main service if you want self-healing on a production server.

### Reverse proxy

Sample configurations for Apache2 and Nginx are in `deploy/`. Both include HTTPS redirection, WebSocket proxying (important for real-time translation progress and entity review), and appropriate timeouts for long-running WebSocket connections.

- **`deploy/apache2-reverse-proxy.conf`** — requires `mod_proxy`, `mod_proxy_http`, `mod_proxy_wstunnel`, `mod_ssl`, `mod_rewrite`
- **`deploy/nginx-reverse-proxy.conf`** — includes a 1-hour `proxy_read_timeout` on `/ws` so the connection survives long entity review waits
- **`deploy/apache2-reader.conf`** — standalone config for exposing only the public library/reader

Replace `t9.example.com` with your actual domain. Both examples assume Let's Encrypt certificates via certbot.

## Environment Variables

Set in `.env` or your shell environment:

| Variable | Description |
|----------|-------------|
| `OPENAI_KEY` | OpenAI API key |
| `ANTHROPIC_KEY` | Anthropic Claude API key |
| `GOOGLE_AI_KEY` | Google Gemini API key |
| `DEEPSEEK_KEY` | DeepSeek API key |
| `OPENROUTER_KEY` | OpenRouter API key |
| `TRANSLATION_MODEL` | Default translation model (e.g. `claude:claude-sonnet-4-6`) |
| `ADVICE_MODEL` | Default entity advice model |
| `T9_PASSWORD` | Enable authentication (required if exposed to the internet) |
| `T9_SECURE_COOKIE` | Set to `true` when serving over HTTPS |
| `DEBUG` | Enable debug logging (`True`/`False`) |

## License

MIT
