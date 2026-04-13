import { useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'

const Section = ({ title, children, defaultOpen = false }) => {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="card overflow-hidden">
      <button
        className="w-full flex items-center gap-2 px-5 py-3 text-left hover:bg-slate-750 transition-colors"
        onClick={() => setOpen(v => !v)}
      >
        {open
          ? <ChevronDown size={14} className="text-slate-500 shrink-0" />
          : <ChevronRight size={14} className="text-slate-500 shrink-0" />}
        <span className="text-sm font-semibold text-slate-200">{title}</span>
      </button>
      {open && (
        <div className="px-5 pb-5 pt-1 text-sm text-slate-300 leading-relaxed space-y-3 border-t border-slate-700/50">
          {children}
        </div>
      )}
    </div>
  )
}

const Kbd = ({ children }) => (
  <span className="px-1.5 py-0.5 rounded bg-slate-700 text-slate-300 text-xs font-mono border border-slate-600">
    {children}
  </span>
)

const Ref = ({ to, children }) => (
  <a href={to} className="text-indigo-400 hover:text-indigo-300 underline underline-offset-2">{children}</a>
)

export default function Help() {
  return (
    <div className="p-6 max-w-3xl mx-auto space-y-4">
      <div className="mb-6">
        <h1 className="text-lg font-semibold text-slate-200">Help & Guide</h1>
        <p className="text-sm text-slate-400 mt-1">
          T9 is a web novel translation tool supporting multiple source languages (Chinese, Japanese, Korean, and more). It uses AI models to translate chapters
          while maintaining consistent terminology across an entire book via an entity management system.
        </p>
      </div>

      {/* ── Recommended Workflow ── */}
      <Section title="Recommended Workflow" defaultOpen={true}>
        <p className="text-slate-400 italic">
          This is the workflow that gets the best results with the least wasted effort.
        </p>

        <div className="space-y-4">
          <div className="flex gap-3">
            <span className="shrink-0 w-6 h-6 rounded-full bg-indigo-600 text-white text-xs font-bold flex items-center justify-center mt-0.5">1</span>
            <div>
              <p className="font-medium text-slate-200">Set up your book</p>
              <p className="text-slate-400">
                Go to <Ref to="/books">Books</Ref> and create a new book. Pick a genre preset &mdash; the genre
                determines the source language and loads an optimized system prompt (e.g. "Chinese Xianxia" includes
                cultivation terminology, "Japanese Light Novel" handles honorifics). You can also choose "Custom"
                and write your own prompt later.
              </p>
            </div>
          </div>

          <div className="flex gap-3">
            <span className="shrink-0 w-6 h-6 rounded-full bg-indigo-600 text-white text-xs font-bold flex items-center justify-center mt-0.5">2</span>
            <div>
              <p className="font-medium text-slate-200">Upload chapters to the queue</p>
              <p className="text-slate-400">
                Go to <Ref to="/queue">Queue</Ref> and use "Upload File" to add text files, or "Upload EPUB" to
                import an entire novel at once. Assign them to your book. You can upload many chapters at once &mdash;
                the queue processes them in order. Chapter numbers are auto-detected from filenames when possible.
              </p>
            </div>
          </div>

          <div className="flex gap-3">
            <span className="shrink-0 w-6 h-6 rounded-full bg-indigo-600 text-white text-xs font-bold flex items-center justify-center mt-0.5">3</span>
            <div>
              <p className="font-medium text-slate-200">Start queue processing</p>
              <p className="text-slate-400">
                Select your models and hit "Process Next" or enable "Auto-process" to translate chapters
                back-to-back. The queue will pause when new entities are found that need review.
              </p>
            </div>
          </div>

          <div className="flex gap-3">
            <span className="shrink-0 w-6 h-6 rounded-full bg-indigo-600 text-white text-xs font-bold flex items-center justify-center mt-0.5">4</span>
            <div>
              <p className="font-medium text-slate-200">Review entities when prompted</p>
              <p className="text-slate-400">
                When the translator finds new proper nouns (character names, places, etc.), it pauses and shows
                you a review panel. Check the translations, fix any mistakes, delete false positives (common words
                misidentified as entities), and approve. The translator then resumes with the corrected entities.
              </p>
            </div>
          </div>

          <div className="flex gap-3">
            <span className="shrink-0 w-6 h-6 rounded-full bg-indigo-600 text-white text-xs font-bold flex items-center justify-center mt-0.5">5</span>
            <div>
              <p className="font-medium text-slate-200">Proofread while the next chapter translates</p>
              <p className="text-slate-400">
                After a chapter finishes, go to <Ref to="/books">Books</Ref>, expand the book, and click
                "Edit" on the just-translated chapter. The Chapter Editor shows the source text on the left and
                English on the right &mdash; you can proofread and fix the translation while the next chapter
                processes in the background. Mark it proofread when you're satisfied.
              </p>
            </div>
          </div>

          <div className="flex gap-3">
            <span className="shrink-0 w-6 h-6 rounded-full bg-indigo-600 text-white text-xs font-bold flex items-center justify-center mt-0.5">6</span>
            <div>
              <p className="font-medium text-slate-200">Export or publish</p>
              <p className="text-slate-400">
                Once all chapters are translated and proofread, export the book as EPUB from the Books page,
                publish to WordPress via the Fictioneer integration, or share via the public library.
              </p>
            </div>
          </div>
        </div>
      </Section>

      {/* ── Pages ── */}
      <Section title="Translate (Dashboard)">
        <p>
          The main translation workspace for one-off translations. Paste source text into the left panel,
          select a book and chapter number, then hit Translate.
        </p>

        <div className="space-y-2">
          <p className="font-medium text-slate-200">Options</p>
          <ul className="list-disc list-inside space-y-1 text-slate-400">
            <li><span className="text-slate-300">Book</span> &mdash; assigns the chapter to a book and uses that book's entities and custom system prompt.</li>
            <li><span className="text-slate-300">Chapter #</span> &mdash; the chapter number. Auto-increments if left blank.</li>
            <li><span className="text-slate-300">Translation model</span> &mdash; the AI model used for the main translation. Overrides the default from Settings.</li>
            <li><span className="text-slate-300">Advice model</span> &mdash; a secondary model consulted for entity translation suggestions (e.g. name romanization). Can be a smaller, cheaper model.</li>
            <li><span className="text-slate-300">Cleaning model</span> &mdash; a lightweight model that double-checks whether newly found entities are actually proper nouns. Recommended for DeepSeek or smaller models which often misidentify common words as entities.</li>
            <li><span className="text-slate-300">Skip entity review</span> &mdash; automatically accept all new entities without pausing for review. Faster, but you lose the chance to fix mistakes before they propagate.</li>
            <li><span className="text-slate-300">Skip entity cleaning</span> &mdash; disable the cleaning pass. Saves a small amount of time/tokens if your translation model is already accurate at identifying entities.</li>
            <li><span className="text-slate-300">Skip partial repair</span> &mdash; after translation, any lines that still contain source-language characters are automatically retranslated using the cleaning model. This is most useful with Chinese-native models like DeepSeek, which occasionally leave fragments untranslated. No extra API calls are used unless untranslated characters are actually detected, so it's safe to leave enabled.</li>
            <li><span className="text-slate-300">Skip unit conversion</span> &mdash; disable the post-translation unit conversion pass. See the Unit Conversion section below for details.</li>
          </ul>
        </div>

        <p className="text-slate-400">
          The right panel shows streaming translation output and a status log. When complete, the translated
          chapter is automatically saved to the book.
        </p>

        <div className="space-y-2">
          <p className="font-medium text-slate-200">JSON fix panel</p>
          <p className="text-slate-400">
            If the AI model returns malformed JSON (which can happen with less capable models), a fix panel
            appears showing the raw response broken into chunks. You can manually correct the JSON or retry the
            failed chunk before continuing.
          </p>
        </div>
      </Section>

      <Section title="Books">
        <p>
          Manages your books, chapters, and book-specific settings.
        </p>

        <div className="space-y-2">
          <p className="font-medium text-slate-200">Book features</p>
          <ul className="list-disc list-inside space-y-1 text-slate-400">
            <li><span className="text-slate-300">Create / Edit</span> &mdash; set title, author, status (ongoing/completed/hiatus/dropped), and language metadata.</li>
            <li><span className="text-slate-300">Genre presets</span> &mdash; when creating a book, pick a genre (Chinese Xianxia, Japanese Light Novel, Korean Web Novel, etc.) to load an optimized system prompt and entity categories. You can customize the prompt after creation.</li>
            <li><span className="text-slate-300">Cover image</span> &mdash; upload a cover image for the book. Used in EPUB exports and the public library.</li>
            <li><span className="text-slate-300">System prompt</span> &mdash; each book can have a custom system prompt that overrides the default translation instructions. Useful for book-specific tone, style, or terminology rules.</li>
            <li><span className="text-slate-300">Entity categories</span> &mdash; customize which entity categories are available for the book (e.g. add "cultivation ranks" for xianxia, or remove unused categories).</li>
            <li><span className="text-slate-300">Export EPUB</span> &mdash; generates an EPUB file from all translated chapters.</li>
            <li><span className="text-slate-300">WordPress publish</span> &mdash; publish to a WordPress/Fictioneer site (see the WordPress section below).</li>
          </ul>
        </div>

        <div className="space-y-2">
          <p className="font-medium text-slate-200">Chapter features</p>
          <ul className="list-disc list-inside space-y-1 text-slate-400">
            <li><span className="text-slate-300">Edit</span> &mdash; opens the Chapter Editor (see below).</li>
            <li><span className="text-slate-300">Proofread status</span> &mdash; green check means proofread, amber dot means not yet reviewed.</li>
            <li><span className="text-slate-300">Batch operations</span> &mdash; select multiple chapters to delete, mark as proofread, or requeue for retranslation.</li>
            <li><span className="text-slate-300">Read</span> &mdash; open a chapter in the Reader view for a clean reading experience.</li>
          </ul>
        </div>

        <div className="space-y-2">
          <p className="font-medium text-slate-200">Global search</p>
          <p className="text-slate-400">
            Click the search icon next to "New Book" (or press <Kbd>Ctrl+F</Kbd> on the Books page) to open the
            global search modal. Select a book, type a query, and results appear grouped by chapter with match counts.
            Click a result to jump directly into the Chapter Editor with the search pre-loaded and positioned on the
            first match, ready to navigate forward through the book.
          </p>
        </div>
      </Section>

      <Section title="Chapter Editor">
        <p>
          A split-pane view for proofreading. The left panel shows the original source text (read-only),
          the right panel has the editable English translation.
        </p>

        <div className="space-y-2">
          <p className="font-medium text-slate-200">Tools</p>
          <ul className="list-disc list-inside space-y-1 text-slate-400">
            <li><span className="text-slate-300">Entity highlighting</span> &mdash; toggle to highlight known entities in both panels with category-specific colors. Click a highlighted entity to edit it inline.</li>
            <li><span className="text-slate-300">Dictionary lookup</span> &mdash; select text and use the toolbar button to look it up in CC-CEDICT (Chinese dictionary).</li>
            <li><span className="text-slate-300">LLM retranslation</span> &mdash; select a source passage and request an AI retranslation. The result appears as ruby text above the original, so you can compare the new translation with the current one.</li>
            <li><span className="text-slate-300">Proofread toggle</span> &mdash; mark the chapter as proofread when you're satisfied with the translation.</li>
          </ul>
        </div>

        <div className="space-y-2">
          <p className="font-medium text-slate-200">Search & Replace</p>
          <p className="text-slate-400">
            Press <Kbd>Ctrl+F</Kbd> (or click the Find button in the toolbar) to open the search bar.
            Press <Kbd>Ctrl+H</Kbd> to open it with the replace field focused.
          </p>
          <ul className="list-disc list-inside space-y-1 text-slate-400">
            <li><span className="text-slate-300">Scope</span> &mdash; search in Translated text, Source text, or Both.</li>
            <li><span className="text-slate-300">Regex</span> &mdash; click <span className="font-mono text-slate-300">.*</span> to toggle regular expression mode.</li>
            <li><span className="text-slate-300">Book-wide search</span> &mdash; click the book icon to search across all chapters. Matches highlight in the current chapter and <Kbd>Enter</Kbd> / <Kbd>Shift+Enter</Kbd> navigate across chapter boundaries automatically.</li>
            <li><span className="text-slate-300">Replace / Replace All</span> &mdash; only available when the scope includes translated text (source text is read-only). Replace All in book-wide mode modifies all chapters at once.</li>
            <li><span className="text-slate-300">Undo</span> &mdash; after a book-wide Replace All, an undo toast appears for 15 seconds. Click it to revert all changes across every affected chapter.</li>
          </ul>
          <p className="text-slate-400">
            Keyboard: <Kbd>Enter</Kbd> next match, <Kbd>Shift+Enter</Kbd> previous match, <Kbd>Escape</Kbd> close search.
          </p>
        </div>
      </Section>

      <Section title="Entities">
        <p>
          The entity system is what makes T9 produce consistent translations across hundreds of chapters.
          Entities are proper nouns &mdash; character names, place names, organizations, abilities, titles, equipment,
          and creatures &mdash; that the translator needs to keep consistent.
        </p>

        <div className="space-y-2">
          <p className="font-medium text-slate-200">How it works</p>
          <p className="text-slate-400">
            During translation, the AI identifies new proper nouns and suggests translations. These are presented
            for review. Once approved, the entity and its translation are stored and included in the system prompt
            for all future chapters of that book, so the AI always uses the same translation.
          </p>
        </div>

        <div className="space-y-2">
          <p className="font-medium text-slate-200">Entity features</p>
          <ul className="list-disc list-inside space-y-1 text-slate-400">
            <li><span className="text-slate-300">Categories</span> &mdash; characters, places, organizations, abilities, titles, equipment, creatures (customizable per book).</li>
            <li><span className="text-slate-300">Book scope</span> &mdash; entities can be book-specific or global (shared across all books).</li>
            <li><span className="text-slate-300">Gender</span> &mdash; for character entities, helps the AI use correct pronouns.</li>
            <li><span className="text-slate-300">Notes</span> &mdash; per-entity translation guidance that gets included in the AI prompt. Keep notes brief and specific, e.g. "Use female pronouns in narration." Noted entities are pinned to the top of each category for visibility.</li>
            <li><span className="text-slate-300">AI advice</span> &mdash; ask a secondary AI model for translation suggestions on any entity.</li>
            <li><span className="text-slate-300">Dictionary lookup</span> &mdash; look up an entity's source text in CC-CEDICT.</li>
            <li><span className="text-slate-300">Duplicate detection</span> &mdash; find entities that share the same source or English text, which may indicate a problem.</li>
            <li><span className="text-slate-300">Propagation</span> &mdash; when you change an entity's translation, you're offered the option to find-and-replace the old translation across all chapters, or re-queue affected chapters for retranslation.</li>
          </ul>
        </div>

        <div className="space-y-2">
          <p className="font-medium text-slate-200">Retroactive entity review</p>
          <p className="text-slate-400">
            Click the review icon on an entity to open the retroactive review modal. This lets you revisit entities
            introduced in earlier chapters with full context &mdash; you can see where the entity first appeared,
            get AI advice on the translation, look it up in the dictionary, set gender, and choose how to propagate
            any changes (do nothing, find-and-replace in forward chapters, or flag forward chapters for retranslation).
          </p>
        </div>
      </Section>

      <Section title="Queue">
        <p>
          Batch processing for translating many chapters in sequence. This is the recommended way to translate
          a book &mdash; upload all your chapters, configure your models, and let it run.
        </p>

        <div className="space-y-2">
          <p className="font-medium text-slate-200">Upload</p>
          <p className="text-slate-400">
            Use "Upload File" to add <span className="text-slate-300">.txt</span> files (single or batch), or
            "Upload EPUB" to import an entire novel &mdash; each chapter is extracted and added as a separate queue item.
            Assign a book and starting chapter number during upload. When uploading EPUB files, you can also create a
            new book directly from the EPUB metadata.
          </p>
        </div>

        <div className="space-y-2">
          <p className="font-medium text-slate-200">Processing options</p>
          <ul className="list-disc list-inside space-y-1 text-slate-400">
            <li><span className="text-slate-300">Filter by book</span> &mdash; only show and process queue items for a specific book.</li>
            <li><span className="text-slate-300">Auto-process</span> &mdash; after each chapter finishes, automatically start the next one. The queue pauses for entity review or when you click "Stop after current."</li>
            <li><span className="text-slate-300">Model selectors</span> &mdash; same as the Dashboard: translation, advice, and cleaning models. These persist in your browser.</li>
            <li><span className="text-slate-300">Skip review / Skip cleaning / Skip partial repair / Skip unit conversion</span> &mdash; same as the Dashboard options.</li>
          </ul>
        </div>

        <p className="text-slate-400">
          When entity review is needed during queue processing, you'll be redirected to the Dashboard to review.
          After approving, translation resumes and the queue continues.
        </p>
      </Section>

      <Section title="Settings">
        <div className="space-y-2">
          <p className="font-medium text-slate-200">API Providers</p>
          <p className="text-slate-400">
            T9 supports multiple AI providers: <span className="text-slate-300">OpenAI</span>,{' '}
            <span className="text-slate-300">DeepSeek</span>,{' '}
            <span className="text-slate-300">Anthropic Claude</span>,{' '}
            <span className="text-slate-300">Google Gemini</span>, and{' '}
            <span className="text-slate-300">OpenRouter</span>.
            Each provider needs an API key set either here or via environment variables.
            Use the "Test" button to verify your keys work.
          </p>
        </div>

        <div className="space-y-2">
          <p className="font-medium text-slate-200">Default models</p>
          <p className="text-slate-400">
            Set the default translation and advice models here. Format is <span className="font-mono text-slate-300">provider:model</span>,
            e.g. <span className="font-mono text-slate-300">claude:claude-sonnet-4-6</span> or <span className="font-mono text-slate-300">gemini:gemini-2.5-flash</span>.
            These defaults can be overridden per-translation on the Dashboard or Queue pages.
          </p>
        </div>

        <div className="space-y-2">
          <p className="font-medium text-slate-200">Unit conversions</p>
          <p className="text-slate-400">
            Unit conversion automatically converts Chinese units (li, jin, zhang, etc.) to metric equivalents
            in the translated text. It works in two steps: first, a regular expression scans for unit patterns;
            then, all matches are sent to the <span className="text-slate-300">cleaning model</span> to filter out
            false positives (e.g. idiomatic phrases that aren't actual measurements). If no cleaning model is set,
            the filtering step is skipped and all regex matches are converted directly.
          </p>
          <p className="text-slate-400">
            The editable JSON block on this page controls conversion factors and the action for each
            unit: <span className="text-slate-300">annotate</span> (keeps the original text and adds a parenthetical,
            e.g. "thirty li (15 km)") or <span className="text-slate-300">replace</span> (substitutes the converted
            value directly). You can also configure whether to use Arabic numerals or English words for converted
            values. Unit conversion can be toggled on or off per translation.
          </p>
        </div>

        <div className="space-y-2">
          <p className="font-medium text-slate-200">Public library</p>
          <p className="text-slate-400">
            When enabled, the Library and Reader pages become accessible without logging in. Visitors can browse
            your translated books and read chapters without needing the app password. The translation tools remain
            protected behind authentication.
          </p>
        </div>

        <div className="space-y-2">
          <p className="font-medium text-slate-200">Debug mode</p>
          <p className="text-slate-400">
            Enables verbose logging for troubleshooting translation issues or API errors.
          </p>
        </div>

        <div className="space-y-2">
          <p className="font-medium text-slate-200">Database export</p>
          <p className="text-slate-400">
            Export all entities as JSON for backup. Useful before making large changes to your entity database.
          </p>
        </div>
      </Section>

      <Section title="Reader">
        <p>
          A clean, distraction-free reading interface for translated books. Accessible from the Books page
          (click "Read") or from the public Library if enabled.
        </p>

        <div className="space-y-2">
          <p className="font-medium text-slate-200">Features</p>
          <ul className="list-disc list-inside space-y-1 text-slate-400">
            <li><span className="text-slate-300">Chapter navigation</span> &mdash; previous/next buttons, swipe gestures on mobile, and arrow key shortcuts.</li>
            <li><span className="text-slate-300">Table of contents</span> &mdash; jump to any chapter from a slide-out panel.</li>
            <li><span className="text-slate-300">Search</span> &mdash; press <Kbd>Ctrl+F</Kbd> to search within the current chapter.</li>
            <li><span className="text-slate-300">Display modes</span> &mdash; view translated text only, source text only, or both interleaved line by line.</li>
            <li><span className="text-slate-300">New entities</span> &mdash; entities introduced in the current chapter are shown as color-coded badges, so you can see which characters or terms appear for the first time.</li>
            <li><span className="text-slate-300">Themes</span> &mdash; light, sepia, and dark reading themes with adjustable font size.</li>
            <li><span className="text-slate-300">Fullscreen</span> &mdash; toggle fullscreen mode for an immersive reading experience.</li>
          </ul>
        </div>
      </Section>

      <Section title="Library (Public)">
        <p>
          An optional public-facing book listing. When enabled in <Ref to="/settings">Settings</Ref>,
          unauthenticated visitors can browse your translated books, view book details, read chapters in
          the Reader, and download EPUB files &mdash; all without needing to log in.
        </p>
        <p className="text-slate-400">
          The Library is entirely separate from the translation tools. Visitors only see finished books
          and chapters &mdash; they cannot access the Dashboard, Queue, Entities, or Settings pages.
        </p>
      </Section>

      {/* ── WordPress ── */}
      <Section title="WordPress / Fictioneer Publishing (Optional)">
        <p>
          T9 can publish translated books directly to a WordPress site running the{' '}
          <Ref to="https://github.com/Tetrakern/fictioneer">Fictioneer</Ref> theme.
          Stories and chapters are created via the WordPress REST API, with a small companion plugin
          that handles Fictioneer-specific metadata (chapter linking, word counts, story ordering).
        </p>
        <p className="text-slate-400">
          This is entirely optional &mdash; you can ignore this section if you only need EPUB export.
        </p>

        <div className="space-y-2">
          <p className="font-medium text-slate-200">What you need</p>
          <ul className="list-disc list-inside space-y-1 text-slate-400">
            <li>WordPress 5.6+ with the <span className="text-slate-300">Fictioneer</span> theme active</li>
            <li>A WordPress user account with <span className="text-slate-300">Editor</span> or <span className="text-slate-300">Administrator</span> role</li>
            <li>HTTPS enabled on the WordPress site (required for Application Passwords)</li>
          </ul>
        </div>

        <div className="space-y-2">
          <p className="font-medium text-slate-200">Step 1 &mdash; Install the T9 plugin on WordPress</p>
          <p className="text-slate-400">
            T9 ships with a small WordPress plugin at <span className="font-mono text-slate-300">deploy/fictioneer-rest-meta.php</span>.
            This plugin adds REST endpoints that let T9 set Fictioneer-specific metadata that the standard
            WordPress API cannot handle (chapter-to-story linking, word counts, story status).
          </p>

          <p className="text-amber-400/80 text-xs mt-2">Same server (T9 and WordPress on the same machine)</p>
          <pre className="bg-slate-900/70 rounded p-3 text-xs font-mono text-slate-300 overflow-x-auto whitespace-pre">
{`cd /path/to/t9
bash deploy/install-wp-plugin.sh /path/to/wordpress`}
          </pre>
          <p className="text-slate-500 text-xs">
            The script copies the plugin, sets file ownership to <span className="font-mono">www-data</span>,
            and activates it via WP-CLI if available. Defaults to <span className="font-mono">/srv/www/wordpress</span> if no path is given.
          </p>

          <p className="text-amber-400/80 text-xs mt-3">Different servers (T9 and WordPress on separate machines)</p>
          <pre className="bg-slate-900/70 rounded p-3 text-xs font-mono text-slate-300 overflow-x-auto whitespace-pre">
{`# Copy the plugin file to the WordPress server
scp deploy/fictioneer-rest-meta.php user@wp-server:/tmp/

# SSH in and run the install script
ssh user@wp-server 'bash -s' < deploy/install-wp-plugin.sh /path/to/wordpress`}
          </pre>
          <p className="text-slate-500 text-xs">
            Alternatively, you can install manually: copy the plugin file
            to <span className="font-mono">wp-content/plugins/fictioneer-rest-meta/fictioneer-rest-meta.php</span> on
            the WordPress server, then activate it in <span className="text-slate-300">WP Admin &gt; Plugins</span>.
          </p>
          <p className="text-slate-500 text-xs">
            A third option is to zip the plugin directory and upload it
            via <span className="text-slate-300">WP Admin &gt; Plugins &gt; Add New &gt; Upload Plugin</span>.
          </p>
        </div>

        <div className="space-y-2">
          <p className="font-medium text-slate-200">Step 2 &mdash; Create a WordPress Application Password</p>
          <p className="text-slate-400">
            T9 authenticates with WordPress using Application Passwords (not your regular login password).
          </p>
          <ol className="list-decimal list-inside space-y-1 text-slate-400">
            <li>Log in to WP Admin.</li>
            <li>Go to <span className="text-slate-300">Users &gt; Profile</span>.</li>
            <li>Scroll to <span className="text-slate-300">Application Passwords</span>.</li>
            <li>Enter a name (e.g. "T9") and click <span className="text-slate-300">Add New Application Password</span>.</li>
            <li>Copy the generated password &mdash; it is only shown once.</li>
          </ol>
          <p className="text-slate-500 text-xs mt-1">
            For local/dev setups without HTTPS, add <span className="font-mono">define('WP_ENVIRONMENT_TYPE', 'local');</span> to
            your <span className="font-mono">wp-config.php</span>.
          </p>
        </div>

        <div className="space-y-2">
          <p className="font-medium text-slate-200">Step 3 &mdash; Configure T9</p>
          <p className="text-slate-400">
            Go to <Ref to="/settings">Settings</Ref> and fill in the <span className="text-slate-300">WordPress / Fictioneer</span> section:
          </p>
          <ul className="list-disc list-inside space-y-1 text-slate-400">
            <li><span className="text-slate-300">WordPress Site URL</span> &mdash; the full URL, e.g. <span className="font-mono text-slate-500">https://novels.example.com</span></li>
            <li><span className="text-slate-300">Username</span> &mdash; your WordPress login username or email</li>
            <li><span className="text-slate-300">Application Password</span> &mdash; the password from step 2</li>
          </ul>
          <p className="text-slate-400">
            Click <span className="text-slate-300">Save</span>, then <span className="text-slate-300">Test Connection</span> to
            verify everything works.
          </p>
        </div>

        <div className="space-y-2">
          <p className="font-medium text-slate-200">Step 4 &mdash; Publish</p>
          <p className="text-slate-400">
            Go to <Ref to="/books">Books</Ref> and click the globe icon on the book you want to publish.
            Set the story status and rating, then click <span className="text-slate-300">Publish All</span>.
            T9 creates (or updates) a Fictioneer story post, uploads the cover image, creates chapter posts,
            links them to the story, and sets the chapter ordering.
          </p>
          <p className="text-slate-400">
            Re-publishing is safe and incremental &mdash; unchanged chapters are skipped, modified chapters are
            updated, and new chapters are created.
          </p>
        </div>

        <div className="space-y-2">
          <p className="font-medium text-slate-200">Troubleshooting</p>
          <ul className="list-disc list-inside space-y-1 text-slate-400">
            <li><span className="text-slate-300">Test connection returns 401</span> &mdash; verify the Application Password is correct and HTTPS is enabled.</li>
            <li><span className="text-slate-300">Chapters show 0 words</span> &mdash; this means the word-count step failed. Re-publish to fix, or use the recalculate endpoint on the WordPress server.</li>
            <li><span className="text-slate-300">Duplicate stories</span> &mdash; if a publish fails midway, re-publishing may create a duplicate story. Delete the extra in WP Admin and clear the publish state: <span className="font-mono text-xs">sqlite3 database.db "DELETE FROM wp_publish_state WHERE book_id = X;"</span></li>
            <li><span className="text-slate-300">Plugin not working after update</span> &mdash; re-run the install script or re-upload the plugin file. The plugin has no settings of its own.</li>
          </ul>
        </div>
      </Section>

      {/* ── Tips ── */}
      <Section title="Tips & Best Practices">
        <ul className="list-disc list-inside space-y-2 text-slate-400">
          <li>
            <span className="text-slate-300">Review entities carefully in early chapters.</span> The first few
            chapters establish the names and terms that every future chapter will use. Mistakes here compound.
          </li>
          <li>
            <span className="text-slate-300">Use entity notes sparingly.</span> Notes are included in every
            AI prompt for that book, so keep them short and only for genuinely tricky cases (e.g. pronoun rules,
            ambiguous names).
          </li>
          <li>
            <span className="text-slate-300">Use the cleaning model for DeepSeek.</span> DeepSeek and smaller
            models tend to flag common words as proper nouns. A cleaning pass with a cheap model
            (e.g. Claude Haiku, gpt-4o-mini) catches most of these.
          </li>
          <li>
            <span className="text-slate-300">Proofread as you go.</span> Translating and proofreading in parallel
            is efficient &mdash; while one chapter translates, edit the previous one.
          </li>
          <li>
            <span className="text-slate-300">Use book-specific system prompts</span> for persistent translation
            style guidance that applies to the entire book, and entity notes for guidance specific to a single
            character or term.
          </li>
          <li>
            <span className="text-slate-300">Check for duplicates periodically.</span> The "Check Duplicates"
            button on the Entities page finds entities that might have been double-entered under different
            categories.
          </li>
          <li>
            <span className="text-slate-300">Use retroactive review</span> if you realize an early entity
            translation was wrong &mdash; it can propagate the fix across all chapters that reference it.
          </li>
          <li>
            <span className="text-slate-300">Leave partial repair enabled.</span> It only costs extra tokens
            when untranslated characters are actually found, so there's no downside to keeping it on.
          </li>
        </ul>
      </Section>
    </div>
  )
}
