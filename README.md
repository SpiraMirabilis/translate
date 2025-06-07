# AI-Powered Web Novel Translator

A comprehensive utility designed to translate web novels (primarily Chinese) to English using multiple AI providers. Features intelligent entity management, book organization, queue-based processing, and support for multiple output formats.

**Supported AI Providers:** OpenAI GPT, DeepSeek, Anthropic Claude, Google Gemini, OpenRouter

The tool can import chapters from various sources: individual files, directories of files, EPUB files (including WebToEpub Chrome extension format), or clipboard content.

## Getting Started

### Prerequisites

- Python 3.8 or higher
- At least one API key from supported providers:
  - OpenAI API key
  - DeepSeek API key  
  - Anthropic Claude API key
  - Google AI API key (for Gemini)
  - OpenRouter API key
- Required Python packages (installed automatically): openai, anthropic, google-generativeai, ebooklib, bs4, questionary, rich, pyperclip

### Installation

1. Clone the repository:
   ```
   git clone https://github.com/SpiraMirabilis/translate
   cd translator
   ```

2. Install dependencies:
   ```
   pip install -r requirements.txt
   ```

3. Create a `.env` file with your API keys:
   ```
   # API Keys (add the ones you have)
   OPENAI_KEY=your_openai_api_key
   DEEPSEEK_KEY=your_deepseek_api_key
   ANTHROPIC_KEY=your_anthropic_api_key
   GOOGLE_AI_KEY=your_google_ai_api_key
   OPENROUTER_KEY=your_openrouter_api_key
   
   # Model Configuration
   TRANSLATION_MODEL=oai:gpt-4-turbo
   ADVICE_MODEL=oai:gpt-3.5-turbo
   
   # Optional Settings
   DEBUG=False
   MAX_CHARS=5000  # Legacy fallback (now per-provider via models.json)
   ```

### Quick Start: Translate a Single Chapter

1. Run the translator with text from clipboard:
   ```
   python translator.py --clipboard
   ```

2. Or translate a text file:
   ```
   python translator.py --file chapter1.txt
   ```

### Quick Start: Translate a Book

1. Create a book in the database:
   ```
   python translator.py --create-book "My Novel" --book-author "Author Name"
   ```
   Note the book ID that is returned (e.g., "Book created: 'My Novel' (ID: 1)")

2. Process EPUB file and add chapters to queue:
   ```
   python translator.py --epub novel.epub --book-id 1
   ```

3. Start sequential translation of all queued chapters:
   ```
   python translator.py --resume
   ```

## Command Line Options

### Input Options

| Option | Description |
|--------|-------------|
| `--clipboard` | Process input from the clipboard |
| `--file FILE` | Process input from the specified file |
| `--resume` | Take input from the queue and translate sequentially |
| `--epub FILE` | Process an EPUB file and add chapters to the queue |
| `--create-book-from-epub` | Create a new book from EPUB metadata when processing an EPUB file |
| `--dir DIRECTORY` | Process all text files in a directory and add to queue |
| `--sort {auto,name,modified,none}` | Sorting strategy for directory files (default: auto) |
| `--pattern PATTERN` | File pattern for directory processing (default: *.txt) |
| `--no-stream` | Disable streaming translation (progress tracking is enabled by default) |

### Book Management

| Option | Description |
|--------|-------------|
| `--create-book TITLE` | Create a new book with the specified title |
| `--book-author AUTHOR` | Specify author when creating a book |
| `--book-language LANG` | Specify language code when creating a book (default: en) |
| `--book-description DESC` | Specify description when creating a book |
| `--list-books` | List all books in the database |
| `--book-info ID` | Get detailed information about a book by ID |
| `--edit-book ID` | Edit book information by ID |
| `--delete-book ID` | Delete a book and all its chapters by ID |

### Chapter Management

| Option | Description |
|--------|-------------|
| `--book-id ID` | Specify book ID for translation or chapter operations |
| `--chapter-number NUM` | Specify chapter number for translation or retrieval |
| `--list-chapters ID` | List all chapters for a book by ID |
| `--get-chapter` | Get a specific chapter (requires --book-id and --chapter-number) |
| `--delete-chapter` | Delete a specific chapter (requires --book-id and --chapter-number) |
| `--export-book ID` | Export all chapters of a book to specified format. e.g. ```python translator.py --export-book 1 --format epub``` |
| `--retranslate` | Retranslate a chapter (requires --book-id and --chapter-number) |

### Queue Management

| Option | Description |
|--------|-------------|
| `--queue` | Add translated content to the queue for later processing |
| `--list-queue` | List all items in the translation queue |
| `--clear-queue` | Clear the translation queue |

### Output Options

| Option | Description |
|--------|-------------|
| `--format {text,html,markdown,epub}` | Output format for translation results (default: text) |
| `--epub-title TITLE` | Book title for EPUB output |
| `--epub-author AUTHOR` | Book author for EPUB output |
| `--epub-language LANG` | Book language code for EPUB output (default: en) |
| `--edit-epub-info` | Edit book information for EPUB output |

### Model Options

| Option | Description |
|--------|-------------|
| `--model MODEL` | Specify model for translation (format: [provider:]model, e.g., oai:gpt-4, deepseek:deepseek-chat, claude:claude-3-5-sonnet, gemini:gemini-2.5-flash, or:qwen/qwen3-235b-a22b) |
| `--advice-model MODEL` | Specify model for entity translation advice |
| `--key KEY` | Specify API key (for the provider specified in --model) |
| `--list-providers` | List all available providers and models |

### Entity Management

| Option | Description |
|--------|-------------|
| `--export-json FILE` | Export SQLite database to JSON file |
| `--import-json FILE` | Import entities from JSON file to SQLite database |
| `--check-duplicates` | Check for duplicate entities in the database |

### Prompt Template Management

| Option | Description |
|--------|-------------|
| `--show-prompt-template ID` | Show the current prompt template for a book (by ID) |
| `--set-prompt-template ID` | Set a custom prompt template for a book (by ID) |
| `--prompt-file FILE` | Load prompt template from a file |
| `--export-default-prompt FILE` | Export the default prompt template to a file |
| `--edit-prompt ID` | Edit the prompt template for a book using your system editor |

## Detailed Usage Examples

### Input Methods

From clipboard (text must be copied first):
```
python translator.py --clipboard
```

From a file:
```
python translator.py --file path/to/chapter.txt
```

Manual input (will prompt for text):
```
python translator.py
```

Process all text files in a directory:
```
python translator.py --dir path/to/chapters --sort auto --pattern "*.txt"
```

Process EPUB and create a book automatically:
```
python translator.py --epub path/to/book.epub --create-book-from-epub
```

### Book Management

Create a new book:
```
python translator.py --create-book "Book Title" --book-author "Author Name" --book-language "en" --book-description "A fantasy novel"
```

List all books:
```
python translator.py --list-books
```

View detailed book information:
```
python translator.py --book-info 1
```

Edit a book:
```
python translator.py --edit-book 1
```

Delete a book:
```
python translator.py --delete-book 1
```

### Chapter Management

List chapters in a book:
```
python translator.py --list-chapters 1
```

Get a specific chapter:
```
python translator.py --get-chapter --book-id 1 --chapter-number 5 --format html
```

Delete a chapter:
```
python translator.py --delete-chapter --book-id 1 --chapter-number 5
```

Export a book to different formats:
```
python translator.py --export-book 1 --format epub
python translator.py --export-book 1 --format html
python translator.py --export-book 1 --format markdown
```

Retranslate a specific chapter:
```
python translator.py --retranslate --book-id 1 --chapter-number 5
```

### Queue Management

Add a file to the translation queue:
```
python translator.py --file chapter.txt --queue
```

Process all files in a directory and add to queue:
```
python translator.py --dir chapters/ --queue
```

List the current queue:
```
python translator.py --list-queue
```

Clear the queue:
```
python translator.py --clear-queue
```

Process the queue sequentially:
```
python translator.py --resume
```

### Model Selection and API Keys

Use OpenAI GPT-4:
```
python translator.py --model oai:gpt-4-turbo --file chapter.txt
```

Use DeepSeek model:
```
python translator.py --model deepseek:deepseek-chat --file chapter.txt
```

Use Claude model:
```
python translator.py --model claude:claude-3-5-sonnet-20241022 --file chapter.txt
```

Use Gemini model:
```
python translator.py --model gemini:gemini-2.5-flash-preview-05-20 --file chapter.txt
```

Use OpenRouter model:
```
python translator.py --model or:qwen/qwen3-235b-a22b --file chapter.txt
```

Use OpenRouter with full provider name:
```
python translator.py --model openrouter:anthropic/claude-3.5-sonnet --file chapter.txt
```

List all available providers and models:
```
python translator.py --list-providers
```

Specify a different API key for this run:
```
python translator.py --model or:qwen/qwen3-235b-a22b --key your_openrouter_key_here --file chapter.txt
```

Use different models for translation and entity advice:
```
python translator.py --model claude:claude-3-5-sonnet --advice-model oai:gpt-3.5-turbo --file chapter.txt
```

Disable streaming mode (enabled by default):
```
python translator.py --model gemini:gemini-2.5-flash --file chapter.txt --no-stream
```

### Entity Management

Check for duplicate entities:
```
python translator.py --check-duplicates
```

Export entities to JSON:
```
python translator.py --export-json entities_backup.json
```

Import entities from JSON:
```
python translator.py --import-json entities_backup.json
```

### Custom Prompt Templates

Show the current prompt template for a book:
```
python translator.py --show-prompt-template 1
```

Export the default prompt template:
```
python translator.py --export-default-prompt custom_prompt.txt
```

Set a custom prompt template for a book:
```
python translator.py --set-prompt-template 1 --prompt-file custom_prompt.txt
```

Edit a book's prompt template in your system editor:
```
python translator.py --edit-prompt 1
```

## Output Formats

Control the output format of translations:

Plain text (default):
```
python translator.py --file chapter.txt --format text
```

HTML:
```
python translator.py --file chapter.txt --format html
```

Markdown:
```
python translator.py --file chapter.txt --format markdown
```

EPUB:
```
python translator.py --file chapter.txt --format epub --epub-title "Book Title" --epub-author "Author"
```

Edit EPUB metadata:
```
python translator.py --edit-epub-info
```

## Technical Details

### Environment Variables

The program can be configured through environment variables in a `.env` file:

**API Keys:**
- `OPENAI_KEY`: Your OpenAI API key
- `DEEPSEEK_KEY`: Your DeepSeek API key
- `ANTHROPIC_KEY`: Your Anthropic Claude API key
- `GOOGLE_AI_KEY`: Your Google AI API key (for Gemini)
- `OPENROUTER_KEY`: Your OpenRouter API key

**Model Configuration:**
- `TRANSLATION_MODEL`: Default model to use (format: [provider:]model)
- `ADVICE_MODEL`: Model for entity translation advice

**Performance Settings:**
- `MAX_CHARS`: Legacy fallback for chunk size (now per-provider via models.json)
- `DEBUG`: Enable debug mode (True/False)

**Per-Provider Chunk Sizes (configured in providers/models.json):**
- OpenAI/DeepSeek/OpenRouter: 5000 characters
- Claude: 8000 characters  
- Gemini: 12,000 characters
- The above are based on each provider's maximum output tokens and can be changed if needed or desired.

### Database Structure

- SQLite database (`entities.db`) stores entities and book/chapter information
- Entity categories: characters, places, organizations, abilities, titles, equipment
- Books table: stores book metadata
- Chapters table: stores chapter content and translation metadata

### AI Provider System

The translator uses a modular provider system supporting multiple AI services:

**Supported Providers:**
- **OpenAI**: GPT-4, GPT-3.5-turbo, o1-mini, o1-preview
- **DeepSeek**: deepseek-chat (via OpenAI-compatible API)
- **Anthropic Claude**: Claude 3.5 Sonnet, Claude 3 Opus, Claude 3 Haiku
- **Google Gemini**: Gemini 2.5 Flash/Pro, Gemini 1.5 Pro/Flash
- **OpenRouter**: Access to 200+ models (Qwen, Claude, GPT-4, Llama, etc.)

**Provider Features:**
- Streaming support with real-time progress tracking
- Structured JSON output for consistent entity extraction
- Per-provider optimization (chunk sizes, safety settings)
- Automatic fallback and error handling

**Configuration:**
Provider settings are managed in `providers/models.json` with per-provider configuration including API endpoints, model lists, and performance optimizations.

### Files and Directories

- `output/`: Generated translations are saved here, organized by book
- `entities.db`: SQLite database for entities and book/chapter data
- `queue.json`: Translation queue for batch processing
- `system_prompt.txt`: System prompt template (can be customized per book)
- `token_ratios.json`: Stats used for progress estimation
- `providers/models.json`: Provider configuration and model definitions
- `.env`: Environment variables and API keys

## Recent Improvements (2025)

### Performance Enhancements
- **Per-provider chunk optimization**: Each AI model uses optimal input sizes
- **Streaming support**: Real-time translation progress with token-by-token display
- **Improved error handling**: Better safety filter and token limit management

### Gemini Integration
- **Full Google Gemini support**: Latest Gemini 2.5 models with structured output
- **Comprehensive safety settings**: Minimized content blocking for fictional content
- **No token limits**: Uses full model capacity (~64k tokens)

### Architecture Improvements
- **Modular provider system**: Easy to add new AI services
- **Per-provider configuration**: Optimized settings for each model type
- **Enhanced entity management**: Better duplicate detection and consistency

