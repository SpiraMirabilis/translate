# Translator User Guide

A comprehensive guide to installing, configuring, and using the Translator utility for translating text and EPUB files.

## Table of Contents

1. [Installation](#installation)
2. [Basic Usage](#basic-usage)
3. [Input Options](#input-options)
4. [Output Formats](#output-formats)
5. [EPUB Processing](#epub-processing)
6. [Translation Queue](#translation-queue)
7. [Entity Management](#entity-management)
8. [Common Workflows](#common-workflows)
9. [Troubleshooting](#troubleshooting)
10. [Advanced Features](#advanced-features)

## Installation

### Prerequisites

- Python 3.8 or higher
- OpenAI API key or DeepSeek API key (for translation)
- pip (Python package manager)

### Step 1: Download the Translator

Clone or download the Translator from the repository:

```bash
git clone https://github.com/SpiraMirabilis/translate
cd translator
```

### Step 2: Install Dependencies

Install all required dependencies:

```bash
pip install -r requirements.txt
```

This will install:
- openai
- python-dotenv
- questionary
- rich
- pyperclip
- ebooklib
- beautifulsoup4
- html2text

### Step 3: Configure API Keys

Create a `.env` file in the translator directory with your API keys:

```
OPENAI_KEY=your_openai_api_key_here
DEEPSEEK_KEY=your_deepseek_api_key_here
TRANSLATION_MODEL=o3-mini
ADVICE_MODEL=o3-mini
DEBUG=False
MAX_CHARS=10000
```

Replace `your_openai_api_key_here` with your actual OpenAI API key, and optionally configure the model names.

### Step 4: Test the Installation

Run a simple test to verify the installation:

```bash
python translator.py --help
```

You should see a list of available commands and options.

## Basic Usage

### Translating a File

To translate a text file:

```bash
python translator.py --file path/to/your/file.txt
```

The translated text will be saved in the default format (text) and copied to your clipboard.

### Translating from Clipboard

To translate text from your clipboard:

```bash
python translator.py --clipboard
```

Copy the text you want to translate before running this command.

### Manual Text Entry

To enter text manually:

```bash
python translator.py
```

Enter or paste your content, then type `ENDEND` on a new line or press Ctrl+D to start translating.

## Input Options

### File Input

```bash
python translator.py --file path/to/your/file.txt
```

### Clipboard Input

```bash
python translator.py --clipboard
```

### EPUB Input

```bash
python translator.py --epub path/to/your/book.epub
```

This will extract chapters from the EPUB and add them to the translation queue.

### Directory Input

```bash
python translator.py --dir path/to/directory
```

This will process all text files in the directory and add them to the translation queue.

#### Directory Sorting Options

Control how files are ordered:

```bash
# Sort by detected chapter numbers, then by name (default)
python translator.py --dir path/to/directory --sort auto

# Sort alphabetically by filename
python translator.py --dir path/to/directory --sort name

# Sort by modification time (oldest first)
python translator.py --dir path/to/directory --sort modified

# Keep original order
python translator.py --dir path/to/directory --sort none
```

#### File Pattern Filtering

Specify which files to process:

```bash
# Process only .md files
python translator.py --dir path/to/directory --pattern "*.md"

# Process only files with specific naming
python translator.py --dir path/to/directory --pattern "chapter_*.txt"
```

## Output Formats

The translator supports multiple output formats:

### Text Format (Default)

```bash
python translator.py --file input.txt --format text
```

Output is saved as a plain text file.

### HTML Format

```bash
python translator.py --file input.txt --format html
```

Output is saved as an HTML file with basic styling.

### Markdown Format

```bash
python translator.py --file input.txt --format markdown
```

Output is saved as a Markdown file.

### EPUB Format

```bash
python translator.py --file input.txt --format epub
```

Output is saved as an EPUB file, which can be read on e-readers.

## EPUB Processing

### Reading from EPUB

To process an EPUB book and add its chapters to the translation queue:

```bash
python translator.py --epub path/to/your/book.epub
```

This will:
1. Extract all chapters from the EPUB
2. Add each chapter to the translation queue
3. Preserve chapter titles and numbers

### Writing to EPUB

To output translations as an EPUB book:

```bash
python translator.py --format epub
```

#### EPUB Book Information

You can set book information for EPUB output:

```bash
python translator.py --format epub --book-title "My Novel" --book-author "Author Name"
```

Or edit book information interactively:

```bash
python translator.py --edit-book-info
```

### Building a Complete EPUB Book

To translate an entire book and save it as EPUB:

1. First, process the EPUB to add chapters to the queue:
   ```bash
   python translator.py --epub input.epub
   ```

2. Edit book information for output:
   ```bash
   python translator.py --edit-book-info
   ```

3. Process all chapters in the queue with EPUB output:
   ```bash
   python translator.py --resume --format epub
   ```

This will process all chapters in the queue sequentially and create a single EPUB file with all translations.

## Translation Queue

### Adding to Queue

Add the current input to the queue without translating:

```bash
python translator.py --file chapter.txt --queue
```

### Processing Queue

Process items from the queue one by one:

```bash
python translator.py --resume
```

### Listing Queue

View all items in the translation queue:

```bash
python translator.py --list-queue
```

### Clearing Queue

Clear all items from the queue:

```bash
python translator.py --clear-queue
```

## Entity Management

The translator maintains a database of entities (characters, places, etc.) to ensure consistent translations.

### Checking for Duplicates

Check for duplicate entities in the database:

```bash
python translator.py --check-duplicates
```

This will show any entities that appear in multiple categories or have duplicate translations.

### Fixing Duplicates

Automatically fix duplicated entities:

```bash
python translator.py --fix-duplicates
```

This will keep the most recently used instance of each duplicated entity.

### Exporting Entities

Export the entity database to JSON:

```bash
python translator.py --export-json entities_backup.json
```

### Importing Entities

Import entities from a JSON file:

```bash
python translator.py --import-json entities_backup.json
```

## Common Workflows

### Translating a Single Document

```bash
python translator.py --file document.txt --format html
```

### Translating a Complete Book from EPUB

```bash
# Add book to queue
python translator.py --epub input.epub

# Set book information
python translator.py --edit-book-info

# Process all chapters in the queue
python translator.py --resume --format epub
```

This will automatically process all items in the queue sequentially until complete.

### Translating a Complete Book from Directory

```bash
# Add all text files to queue
python translator.py --dir path/to/chapters --sort auto

# Set book information
python translator.py --edit-book-info

# Process all chapters in the queue
python translator.py --resume --format epub
```

This will process all text files in the directory and create an EPUB book.

### Batch Processing with Different Options

If you want to process different chapters with different settings, you can create a script:

```bash
#!/bin/bash
# First set of chapters with one format
python translator.py --resume --format html

# Then process additional chapters with a different format
python translator.py --resume --format epub
```

Save this as `process_queue.sh`, make it executable with `chmod +x process_queue.sh`, and run it with `./process_queue.sh`.

## Troubleshooting

### API Errors

If you see API errors:
- Check that your API keys are correctly set in the `.env` file
- Verify you have sufficient credits/quota on your API account
- Try using a different model (e.g., `--model gpt-3.5-turbo`)

### EPUB Issues

If EPUB processing fails:
- Check that the EPUB file is valid and not DRM-protected
- Try converting the EPUB to a different format and back using Calibre
- Use `--epub-no-images` option if the EPUB contains many images

### Output Formatting Issues

If the output format is incorrect:
- Check that you have the required dependencies installed
- Ensure the output directory is writable
- Try a different output format as a test

### Entity Duplication Issues

If you notice inconsistent translations:
- Run `--check-duplicates` to identify duplicate entities
- Use `--fix-duplicates` to automatically resolve duplications
- Edit the entities manually through the review process

## Advanced Features

### Using Different Translation Models

Specify a different model for translation:

```bash
python translator.py --file input.txt --model gpt-4-turbo
```

### Using a Different API Key

Use a different API key for a single translation:

```bash
python translator.py --file input.txt --key your_alternative_api_key
```

### Debug Mode

Enable debug mode for more detailed logging:

```bash
DEBUG=True python translator.py --file input.txt
```

Or set `DEBUG=True` in your `.env` file.

### Custom Output Directory

If you want to change where output files are saved, you can modify the OutputFormatter class to use a different directory.

---

## Additional Resources

- [OpenAI API Documentation](https://platform.openai.com/docs/api-reference)
- [DeepSeek API Documentation](https://platform.deepseek.com/docs)
- [EPUB Specification](https://www.w3.org/publishing/epub3/epub-spec.html)

For further assistance, please open an issue on the GitHub repository.
