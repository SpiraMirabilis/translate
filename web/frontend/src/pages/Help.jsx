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
          T9 is a Chinese-to-English web novel translation tool. It uses AI models to translate chapters
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
                Go to <Ref to="/books">Books</Ref> and create a new book. Optionally set a custom system prompt
                for the book if you have specific translation instructions (e.g. tone, naming conventions).
              </p>
            </div>
          </div>

          <div className="flex gap-3">
            <span className="shrink-0 w-6 h-6 rounded-full bg-indigo-600 text-white text-xs font-bold flex items-center justify-center mt-0.5">2</span>
            <div>
              <p className="font-medium text-slate-200">Upload chapters to the queue</p>
              <p className="text-slate-400">
                Go to <Ref to="/queue">Queue</Ref> and use "Upload File" to add text files or an EPUB.
                Assign them to your book. You can upload many chapters at once — the queue processes them in order.
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
                "Edit" on the just-translated chapter. The Chapter Editor shows Chinese on the left and English
                on the right — you can proofread and fix the translation while the next chapter processes in the
                background. Mark it proofread when you're satisfied.
              </p>
            </div>
          </div>

          <div className="flex gap-3">
            <span className="shrink-0 w-6 h-6 rounded-full bg-indigo-600 text-white text-xs font-bold flex items-center justify-center mt-0.5">6</span>
            <div>
              <p className="font-medium text-slate-200">Export when ready</p>
              <p className="text-slate-400">
                Once all chapters are translated and proofread, export the book as EPUB from the Books page.
              </p>
            </div>
          </div>
        </div>
      </Section>

      {/* ── Pages ── */}
      <Section title="Translate (Dashboard)">
        <p>
          The main translation workspace for one-off translations. Paste Chinese text into the left panel,
          select a book and chapter number, then hit Translate.
        </p>

        <div className="space-y-2">
          <p className="font-medium text-slate-200">Options</p>
          <ul className="list-disc list-inside space-y-1 text-slate-400">
            <li><span className="text-slate-300">Book</span> — assigns the chapter to a book and uses that book's entities and custom system prompt.</li>
            <li><span className="text-slate-300">Chapter #</span> — the chapter number. Auto-increments if left blank.</li>
            <li><span className="text-slate-300">Translation model</span> — the AI model used for the main translation. Overrides the default from Settings.</li>
            <li><span className="text-slate-300">Advice model</span> — a secondary model consulted for entity translation suggestions (e.g. name romanization). Can be a smaller, cheaper model.</li>
            <li><span className="text-slate-300">Cleaning model</span> — a lightweight model that double-checks whether newly found entities are actually proper nouns. Recommended for DeepSeek or smaller models which often misidentify common words as entities.</li>
            <li><span className="text-slate-300">Skip entity review</span> — automatically accept all new entities without pausing for review. Faster, but you lose the chance to fix mistakes before they propagate.</li>
            <li><span className="text-slate-300">Skip entity cleaning</span> — disable the cleaning pass. Saves a small amount of time/tokens if your translation model is already accurate at identifying entities.</li>
          </ul>
        </div>

        <p className="text-slate-400">
          The right panel shows streaming translation output and a status log. When complete, the translated
          chapter is automatically saved to the book.
        </p>
      </Section>

      <Section title="Books">
        <p>
          Manages your books, chapters, and book-specific settings.
        </p>

        <div className="space-y-2">
          <p className="font-medium text-slate-200">Book features</p>
          <ul className="list-disc list-inside space-y-1 text-slate-400">
            <li><span className="text-slate-300">Create / Edit</span> — set title, author, and language metadata.</li>
            <li><span className="text-slate-300">System prompt</span> — each book can have a custom system prompt that overrides the default translation instructions. Useful for book-specific tone, style, or terminology rules.</li>
            <li><span className="text-slate-300">Export EPUB</span> — generates an EPUB file from all translated chapters.</li>
          </ul>
        </div>

        <div className="space-y-2">
          <p className="font-medium text-slate-200">Chapter features</p>
          <ul className="list-disc list-inside space-y-1 text-slate-400">
            <li><span className="text-slate-300">Edit</span> — opens the Chapter Editor (see below).</li>
            <li><span className="text-slate-300">Proofread status</span> — green check means proofread, amber dot means not yet reviewed.</li>
            <li><span className="text-slate-300">Delete</span> — removes a chapter from the book.</li>
          </ul>
        </div>
      </Section>

      <Section title="Chapter Editor">
        <p>
          A split-pane view for proofreading. The left panel shows the original Chinese (read-only),
          the right panel has the editable English translation.
        </p>

        <div className="space-y-2">
          <p className="font-medium text-slate-200">Tools</p>
          <ul className="list-disc list-inside space-y-1 text-slate-400">
            <li><span className="text-slate-300">Entity highlighting</span> — toggle to highlight known entities in both panels with category-specific colors. Click a highlighted entity to edit it inline.</li>
            <li><span className="text-slate-300">Dictionary lookup</span> — double-click a Chinese word or select text and use the toolbar button to look it up in CC-CEDICT.</li>
            <li><span className="text-slate-300">LLM retranslation</span> — select a Chinese passage and request an AI retranslation. The result appears as ruby text above the original, so you can compare.</li>
            <li><span className="text-slate-300">Proofread toggle</span> — mark the chapter as proofread when you're satisfied with the translation.</li>
          </ul>
        </div>
      </Section>

      <Section title="Entities">
        <p>
          The entity system is what makes T9 produce consistent translations across hundreds of chapters.
          Entities are proper nouns — character names, place names, organizations, abilities, titles, equipment,
          and creatures — that the translator needs to keep consistent.
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
            <li><span className="text-slate-300">Categories</span> — characters, places, organizations, abilities, titles, equipment, creatures.</li>
            <li><span className="text-slate-300">Book scope</span> — entities can be book-specific or global (shared across all books).</li>
            <li><span className="text-slate-300">Gender</span> — for character entities, helps the AI use correct pronouns.</li>
            <li><span className="text-slate-300">Notes</span> — per-entity translation guidance that gets included in the AI prompt. Keep notes brief and specific, e.g. "Use female pronouns in narration." Noted entities are pinned to the top of each category for visibility.</li>
            <li><span className="text-slate-300">AI advice</span> — ask a secondary AI model for translation suggestions on any entity.</li>
            <li><span className="text-slate-300">Dictionary lookup</span> — look up an entity's Chinese text in CC-CEDICT.</li>
            <li><span className="text-slate-300">Duplicate detection</span> — find entities that share the same Chinese or English text, which may indicate a problem.</li>
            <li><span className="text-slate-300">Propagation</span> — when you change an entity's translation, you're offered the option to find-and-replace the old translation across all chapters, or re-queue affected chapters for retranslation.</li>
          </ul>
        </div>
      </Section>

      <Section title="Queue">
        <p>
          Batch processing for translating many chapters in sequence. This is the recommended way to translate
          a book — upload all your chapters, configure your models, and let it run.
        </p>

        <div className="space-y-2">
          <p className="font-medium text-slate-200">Upload</p>
          <p className="text-slate-400">
            Use "Upload File" to add <span className="text-slate-300">.txt</span> files or an <span className="text-slate-300">.epub</span> file.
            For EPUB, each chapter is extracted and added as a separate queue item. Assign a book and starting
            chapter number during upload.
          </p>
        </div>

        <div className="space-y-2">
          <p className="font-medium text-slate-200">Processing options</p>
          <ul className="list-disc list-inside space-y-1 text-slate-400">
            <li><span className="text-slate-300">Filter by book</span> — only show and process queue items for a specific book.</li>
            <li><span className="text-slate-300">Auto-process</span> — after each chapter finishes, automatically start the next one. The queue pauses for entity review or when you click "Stop after current."</li>
            <li><span className="text-slate-300">Model selectors</span> — same as the Dashboard: translation, advice, and cleaning models. These persist in your browser.</li>
            <li><span className="text-slate-300">Skip review / Skip cleaning</span> — same as the Dashboard options.</li>
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
            T9 supports multiple AI providers: <span className="text-slate-300">OpenAI</span> (GPT-4, etc.),{' '}
            <span className="text-slate-300">DeepSeek</span>,{' '}
            <span className="text-slate-300">Anthropic Claude</span>, and{' '}
            <span className="text-slate-300">Google Gemini</span>.
            Each provider needs an API key set either here or via environment variables.
            Use the "Test" button to verify your keys work.
          </p>
        </div>

        <div className="space-y-2">
          <p className="font-medium text-slate-200">Default models</p>
          <p className="text-slate-400">
            Set the default translation and advice models here. Format is <span className="font-mono text-slate-300">provider:model</span>,
            e.g. <span className="font-mono text-slate-300">claude:claude-sonnet-4-6</span> or <span className="font-mono text-slate-300">deepseek:deepseek-chat</span>.
            These defaults can be overridden per-translation on the Dashboard or Queue pages.
          </p>
        </div>

        <div className="space-y-2">
          <p className="font-medium text-slate-200">Database export</p>
          <p className="text-slate-400">
            Export all entities as JSON for backup. Useful before making large changes to your entity database.
          </p>
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
            models tend to flag common Chinese words as proper nouns. A cleaning pass with a cheap model
            (e.g. Claude Haiku, gpt-4o-mini) catches most of these.
          </li>
          <li>
            <span className="text-slate-300">Proofread as you go.</span> Translating and proofreading in parallel
            is efficient — while one chapter translates, edit the previous one.
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
        </ul>
      </Section>
    </div>
  )
}
