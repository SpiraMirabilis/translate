# T9 CLI Reference

The command-line interface for T9. All commands are run via `python3 translator.py`.

> **Note:** The web GUI is the recommended interface for most users. See the main [README.md](README.md) for setup instructions. The CLI is useful for scripting, automation, or when you prefer a terminal workflow.

## Translation

### Input sources

```bash
# Translate from a text file
python3 translator.py --file chapter.txt --book-id 1

# Translate from clipboard
python3 translator.py --clipboard --book-id 1

# Translate with manual text input (interactive prompt)
python3 translator.py --book-id 1

# Specify a chapter number (auto-increments if omitted)
python3 translator.py --file chapter.txt --book-id 1 --chapter-number 42
```

### Model selection

Models use the format `provider:model-name`. Providers can be referenced by full name or alias.

```bash
# Use a specific model
python3 translator.py --model claude:claude-sonnet-4-6 --file chapter.txt

# Use a different advice model (for entity translation suggestions)
python3 translator.py --model gemini:gemini-2.5-flash --advice-model oai:gpt-5-mini --file chapter.txt

# Use a different cleaning model (for filtering false-positive entities)
python3 translator.py --model claude:claude-sonnet-4-6 --cleaning-model claude:claude-haiku-4-5 --file chapter.txt

# Specify an API key for this run only
python3 translator.py --model or:qwen/qwen3-235b-a22b --key sk-or-... --file chapter.txt

# List all available providers and models
python3 translator.py --list-providers
```

**Provider aliases:** `oai` (OpenAI), `claude` (Anthropic), `gemini` (Google), `ds` (DeepSeek), `or` (OpenRouter)

### Translation options

```bash
# Skip entity review (auto-accept all new entities)
python3 translator.py --file chapter.txt --book-id 1 --no-review

# Skip entity cleaning pass
python3 translator.py --file chapter.txt --book-id 1 --no-clean

# Disable streaming (slightly faster for very short texts)
python3 translator.py --file chapter.txt --no-stream

# Disable audio notifications for entity review
python3 translator.py --file chapter.txt --silent-notifications
```

### Output format

```bash
# Default is plain text. Also supports html, markdown, epub.
python3 translator.py --file chapter.txt --format html
python3 translator.py --file chapter.txt --format markdown
python3 translator.py --file chapter.txt --format epub --epub-title "Title" --epub-author "Author"
```

## Queue Processing

The queue lets you batch-translate many chapters in sequence.

```bash
# Add a file to the queue (requires --book-id)
python3 translator.py --file chapter.txt --queue --book-id 1

# Add a file with a specific chapter number
python3 translator.py --file chapter.txt --queue --book-id 1 --chapter-number 5

# Process an EPUB and add all chapters to the queue
python3 translator.py --epub novel.epub --book-id 1

# Process an EPUB and auto-create a book from its metadata
python3 translator.py --epub novel.epub --create-book-from-epub

# Add all text files from a directory to the queue
python3 translator.py --dir chapters/ --book-id 1
python3 translator.py --dir chapters/ --book-id 1 --sort name --pattern "*.txt"

# List queue contents
python3 translator.py --list-queue
python3 translator.py --list-queue --book-id 1

# Process the queue (translate all items sequentially)
python3 translator.py --resume
python3 translator.py --resume --book-id 1

# Clear the queue
python3 translator.py --clear-queue
python3 translator.py --clear-queue --book-id 1
```

### Directory sort options

When using `--dir`, files are sorted before being added to the queue:

| Sort | Description |
|------|-------------|
| `auto` | Try to extract chapter numbers from filenames (default) |
| `name` | Sort alphabetically by filename |
| `modified` | Sort by file modification time |
| `none` | No sorting, filesystem order |

## Book Management

```bash
# Create a book
python3 translator.py --create-book "Book Title" --book-author "Author Name"
python3 translator.py --create-book "Book Title" --book-author "Author" --book-language en --book-description "A fantasy novel"

# List all books
python3 translator.py --list-books

# View book details
python3 translator.py --book-info 1

# Edit book metadata (interactive)
python3 translator.py --edit-book 1

# Delete a book and all its chapters
python3 translator.py --delete-book 1
```

## Chapter Management

```bash
# List chapters in a book
python3 translator.py --list-chapters 1

# View a specific chapter
python3 translator.py --get-chapter --book-id 1 --chapter-number 5
python3 translator.py --get-chapter --book-id 1 --chapter-number 5 --format html

# Delete a chapter
python3 translator.py --delete-chapter --book-id 1 --chapter-number 5

# Retranslate a chapter
python3 translator.py --retranslate --book-id 1 --chapter-number 5

# Edit a chapter's translation in your system editor ($EDITOR)
python3 translator.py --edit-chapter-translation --book-id 1 --chapter-number 5

# Export an entire book
python3 translator.py --export-book 1 --format epub
python3 translator.py --export-book 1 --format text
python3 translator.py --export-book 1 --format html
python3 translator.py --export-book 1 --format markdown
```

## Entity Management

Entities are proper nouns (character names, places, organizations, etc.) tracked for translation consistency.

```bash
# Review all entities interactively
python3 translator.py --review-entities

# Review entities filtered by book and/or category
python3 translator.py --review-entities --entity-book-id 1
python3 translator.py --review-entities --entity-book-id 1 --entity-category characters

# Check for duplicate entities
python3 translator.py --check-duplicates

# Export entities to JSON (backup)
python3 translator.py --export-json backup.json

# Import entities from JSON
python3 translator.py --import-json backup.json
```

### Entity categories

`characters`, `places`, `organizations`, `abilities`, `titles`, `equipment`, `creatures`

## Custom System Prompts

Each book can have its own system prompt that overrides the default translation instructions.

```bash
# View the current prompt for a book
python3 translator.py --show-prompt-template 1

# Export the default prompt to a file (to use as a starting point)
python3 translator.py --export-default-prompt my_prompt.txt

# Set a custom prompt from a file
python3 translator.py --set-prompt-template 1 --prompt-file my_prompt.txt

# Edit a book's prompt in your system editor
python3 translator.py --edit-prompt 1
```

The system prompt supports these template variables:
- `{{ENTITIES_JSON}}` — replaced with the entity glossary for the current chapter
- `{{CHAPTER_NUMBER}}` — replaced with the chapter number

## Environment Variables

Set in a `.env` file in the project root:

```env
# API keys (at least one required)
OPENAI_KEY=sk-...
ANTHROPIC_KEY=sk-ant-...
GOOGLE_AI_KEY=AIza...
DEEPSEEK_KEY=sk-...
OPENROUTER_KEY=sk-or-...

# Default models (optional, can also use --model flag)
TRANSLATION_MODEL=claude:claude-sonnet-4-6
ADVICE_MODEL=oai:gpt-5-mini

# Debug logging
DEBUG=False
```

## Quick Reference

| Task | Command |
|------|---------|
| Translate a file | `python3 translator.py --file ch.txt --book-id 1` |
| Translate from clipboard | `python3 translator.py --clipboard --book-id 1` |
| Process queue | `python3 translator.py --resume` |
| Ingest EPUB to queue | `python3 translator.py --epub book.epub --book-id 1` |
| List books | `python3 translator.py --list-books` |
| Create a book | `python3 translator.py --create-book "Title"` |
| Export book as EPUB | `python3 translator.py --export-book 1 --format epub` |
| List entities | `python3 translator.py --review-entities --entity-book-id 1` |
| Check duplicates | `python3 translator.py --check-duplicates` |
| List providers | `python3 translator.py --list-providers` |
