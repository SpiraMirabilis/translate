# T9 — Chinese Web Novel Translator

T9 is an AI-powered tool for translating Chinese web novels into English. It uses large language models (OpenAI, Claude, Gemini, DeepSeek, OpenRouter) to translate chapters while maintaining consistent terminology across an entire book through an entity management system that tracks character names, places, organizations, and other proper nouns.

## Features

- **Multi-provider AI translation** — OpenAI, Anthropic Claude, Google Gemini, DeepSeek, and OpenRouter
- **Entity consistency** — automatically identifies proper nouns and maintains a per-book glossary so names, places, and terms stay consistent across hundreds of chapters
- **Entity review** — after each chapter, review newly discovered entities, fix translations, and delete false positives before they propagate
- **Entity notes** — attach translation guidance to specific entities (e.g. pronoun rules) that gets included in every AI prompt
- **Entity cleaning** — optional second-pass with a lightweight model to filter out common words misidentified as entities
- **Queue processing** — upload many chapters and translate them back-to-back with auto-processing
- **Book management** — organize translations into books with per-book custom system prompts, chapter tracking, and EPUB export
- **Chapter editor** — split-pane proofreading view with Chinese source on the left, editable English on the right, entity highlighting, CC-CEDICT dictionary lookup, and LLM retranslation
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

## Recommended Workflow

1. **Create a book** — go to Books and create a new book with a title and author. Optionally set a custom system prompt for book-specific translation instructions.

2. **Upload chapters to the queue** — go to Queue and use "Upload File" to add `.txt` files or an `.epub`. Assign them to your book. You can upload many chapters at once.

3. **Start queue processing** — select your models, enable "Auto-process", and hit "Process Next". The queue translates chapters in order, pausing when new entities need review.

4. **Review entities when prompted** — the translator pauses when it finds new proper nouns. Check the translations, fix mistakes, delete false positives (common words misidentified as entities), and approve. Translation resumes with the corrected entities.

5. **Proofread while the next chapter translates** — after a chapter finishes, go to Books, expand the book, and click Edit on the just-translated chapter. The split-pane editor shows Chinese on the left and English on the right. Proofread and fix the translation while the next chapter processes in the background. Mark it proofread when satisfied.

6. **Export** — once all chapters are translated and proofread, export the book as EPUB from the Books page.

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
- **Books** — book and chapter management, custom system prompts, EPUB export
- **Chapter Editor** — split-pane proofreading with entity highlighting, dictionary lookup, and LLM retranslation
- **Entities** — browse, search, edit, and manage the entity glossary with duplicate detection and translation propagation
- **Queue** — batch upload and auto-process chapters with progress tracking
- **Settings** — API provider configuration, default models, database export
- **Help** — built-in guide with recommended workflow and feature reference

## Project Structure

```
t9/
├── start_web.sh           # Launch script (starts both servers)
├── translator.py          # CLI entry point
├── translation_engine.py  # Core translation logic
├── database.py            # SQLite database manager
├── config.py              # Configuration
├── cli.py                 # Command-line interface
├── ui.py                  # Abstract UI base class
├── system_prompt.txt      # Default translation system prompt
├── providers/             # AI provider modules
│   ├── factory.py
│   ├── models.json        # Provider and model configuration
│   ├── openai_provider.py
│   ├── claude_provider.py
│   └── gemini_provider.py
├── web/                   # Web GUI
│   ├── app.py             # FastAPI application
│   ├── requirements_web.txt
│   ├── api/               # REST + WebSocket endpoints
│   ├── services/          # Job manager, web interface
│   └── frontend/          # React + Vite + Tailwind CSS
├── requirements.txt       # Python dependencies (core + CLI)
└── database.db            # SQLite database (created on first run)
```

## CLI

T9 also has a full command-line interface that supports all operations — translation, book/chapter management, entity review, queue processing, and more. See **[CLIReadme.md](CLIReadme.md)** for complete documentation.

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
| `DEBUG` | Enable debug logging (`True`/`False`) |

## License

MIT
