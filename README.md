# T9 — Web Novel Translator

T9 is an AI-powered tool for translating web novels into English. It supports Chinese, Japanese, and Korean source languages using large language models (OpenAI, Claude, Gemini, DeepSeek, OpenRouter) and maintains consistent terminology across an entire book through an entity management system that tracks character names, places, organizations, and other proper nouns.

## Features

- **Multi-provider AI translation** — OpenAI, Anthropic Claude, Google Gemini, DeepSeek, and OpenRouter
- **Multi-language support** — Chinese, Japanese, and Korean source languages with genre-specific prompt presets (xianxia, light novel, Korean web novel, etc.)
- **Entity consistency** — automatically identifies proper nouns and maintains a per-book glossary so names, places, and terms stay consistent across hundreds of chapters
- **Entity review** — after each chapter, review newly discovered entities, fix translations, and delete false positives before they propagate
- **Entity cleaning** — optional second-pass with a lightweight model to filter out common words misidentified as entities
- **Queue processing** — upload many chapters and translate them back-to-back with auto-processing
- **Book management** — organize translations into books with per-book custom system prompts, entity categories, chapter tracking, and EPUB export
- **Chapter editor** — split-pane proofreading view with source on the left, editable English on the right, entity highlighting, dictionary lookup, and LLM retranslation
- **Search & replace** — find text across a single chapter or an entire book, with regex support, cross-chapter navigation, and one-click undo for bulk replacements
- **WordPress publishing** — publish translated books directly to a WordPress site running the Fictioneer theme, with incremental updates
- **Streaming output** — real-time translation progress with chunk-by-chunk status updates
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
- **Text files** — click "Upload File" to add individual `.txt` files. You can upload many at once. Set the book and starting chapter number.
- **Paste** — for a single chapter, you can also go to the **Translate** page, paste the source text directly, and translate it there.

### 3. Start translating

On the Queue page, select your translation model (and optionally an advice model for entity suggestions and a cleaning model for filtering false-positive entities). Click **Process Next** to translate one chapter, or enable **Auto-process** to translate them back-to-back.

### 4. Review entities

When the translator finds new proper nouns — character names, places, organizations — it pauses and shows you a review panel. Check each translation, fix mistakes, delete any common words that were misidentified as entities, and click **Submit**. Translation resumes with the corrected glossary, and those terms stay consistent for every future chapter.

### 5. Proofread

After a chapter finishes, go to **Books**, expand the book, and click **Edit** on the chapter. The split-pane editor shows the source text on the left and the editable English translation on the right. You can proofread and fix the translation while the next chapter processes in the background. Mark it proofread when you're satisfied.

### 6. Export

Once all chapters are translated and proofread, click **Export EPUB** on the Books page to generate an ebook. You can also publish directly to WordPress if you have the Fictioneer integration set up (see below).

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

- **Translate** — single-chapter translation workspace with model selection, streaming output, and entity review
- **Books** — book and chapter management, custom system prompts, EPUB export, global cross-chapter search
- **Chapter Editor** — split-pane proofreading with entity highlighting, dictionary lookup, LLM retranslation, and search & replace
- **Entities** — browse, search, edit, and manage the entity glossary with duplicate detection and translation propagation
- **Queue** — batch upload and auto-process chapters with progress tracking
- **Settings** — API provider configuration, default models, database export, WordPress connection
- **Help** — built-in guide with feature reference and troubleshooting

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
├── genres.json            # Genre preset definitions
├── genres.py              # Genre loading utilities
├── prompts/               # System prompt templates per genre
│   ├── chinese_xianxia.txt
│   ├── japanese_ln.txt
│   └── korean_wn.txt
├── providers/             # AI provider modules
│   ├── factory.py
│   ├── models.json        # Provider and model configuration
│   ├── openai_provider.py
│   ├── claude_provider.py
│   └── gemini_provider.py
├── web/                   # Web GUI
│   ├── app.py             # FastAPI application
│   ├── auth.py            # Session-based authentication
│   ├── requirements_web.txt
│   ├── api/               # REST + WebSocket endpoints
│   ├── services/          # Job manager, web interface
│   └── frontend/          # React + Vite + Tailwind CSS
├── deploy/                # Deployment configs
│   ├── t9.service         # systemd --user service
│   ├── nginx-reverse-proxy.conf
│   ├── apache2-reverse-proxy.conf
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

### Reverse proxy

Sample configurations for Apache2 and Nginx are in `deploy/`. Both include HTTPS redirection, WebSocket proxying (important for real-time translation progress and entity review), and appropriate timeouts for long-running WebSocket connections.

- **`deploy/apache2-reverse-proxy.conf`** — requires `mod_proxy`, `mod_proxy_http`, `mod_proxy_wstunnel`, `mod_ssl`, `mod_rewrite`
- **`deploy/nginx-reverse-proxy.conf`** — includes a 1-hour `proxy_read_timeout` on `/ws` so the connection survives long entity review waits

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
