# Translator User Guide

This is a utility designed to translate web novels to english. Mostly it is designed to translate Chinese web novels.
It is capable of importing chapters of untranslated novels as a batch, either from all files in a directory if you scraped it yourself
or out of an epub file, in the format that WebToEpub Chrome extension outputs (https://github.com/dteviot/WebToEpub || https://chromewebstore.google.com/detail/webtoepub/akiljllkbielkidmammnifcnibaigelm)

## Getting Started

### Prerequisites

- Python 3.8 or higher
- OpenAI API key or DeepSeek API key
- Required Python packages: openai, ebooklib, bs4, questionary, rich

### Installation

1. Clone the repository:
   ```
   git clone https://github.com/yourusername/translator.git
   cd translator
   ```

2. Install dependencies:
   ```
   pip install -r requirements.txt
   ```

3. Create a `.env` file with your API keys:
   ```
   OPENAI_KEY=your_openai_api_key
   DEEPSEEK_KEY=your_deepseek_api_key
   TRANSLATION_MODEL=oai:gpt-4-turbo
   ADVICE_MODEL=oai:gpt-3.5-turbo
   DEBUG=False
   MAX_CHARS=5000
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
| `--model MODEL` | Specify model for translation (format: [provider:]model, e.g., oai:gpt-4 or deepseek:deepseek-chat) |
| `--advice-model MODEL` | Specify model for entity translation advice |
| `--key KEY` | Specify API key (for the provider specified in --model) |

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

Specify a different API key for this run:
```
python translator.py --model oai:gpt-4-turbo --key your_api_key_here --file chapter.txt
```

Use different models for translation and entity advice:
```
python translator.py --model oai:gpt-4-turbo --advice-model oai:gpt-3.5-turbo --file chapter.txt
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

- `OPENAI_KEY`: Your OpenAI API key
- `DEEPSEEK_KEY`: Your DeepSeek API key
- `TRANSLATION_MODEL`: Default model to use (format: [provider:]model)
- `ADVICE_MODEL`: Model for entity translation advice
- `MAX_CHARS`: Maximum characters per API call (default: 5000)
- `DEBUG`: Enable debug mode (True/False)

### Database Structure

- SQLite database (`entities.db`) stores entities and book/chapter information
- Entity categories: characters, places, organizations, abilities, titles, equipment
- Books table: stores book metadata
- Chapters table: stores chapter content and translation metadata

### Files and Directories

- `output/`: Generated translations are saved here
- `entities.db`: SQLite database for entities and book/chapter data
- `queue.json`: Translation queue
- `system_prompt.txt`: System prompt template (can be customized)
- `token_ratios.json`: Stats used for progress estimation

