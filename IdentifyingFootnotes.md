# Identifying and Adding Footnotes

How we find, judge, write, and place cultural-reference footnotes for a translated book. This is **additive annotation only** — it never rewrites the translation. (Entity/translation repair is a separate workflow: `TranslationRepairTask.md`.)

## Goal

Add **first-mention footnotes** for Chinese cultural referents an English reader won't have, so the prose stays readable without in-line explanation.

**Density cap: 0–4 footnotes per chapter.** This isn't arXiv. When in doubt, cut. Most candidates should be rejected — a full pass over book 15 (932 chapters) kept **188 of 674 candidates, about 28%**, and that felt right, not stingy.

---

## Two sources of candidates — you need both

### 1. The entity pull (per review batch)

```
python3 get_entities.py -b <id> --origin-chapter <range> --format text
```

Footnote candidates surface as books / creatures / items / places / titles that are cultural referents.

⚠️ **`--origin-chapter` under-reports — the range pull is not complete.** `origin_chapter` records when an entity was *extracted*, not when it first *appears*, so an entity whose first mention is in your range can be filed under a later chapter (or none) and silently miss the batch. Refresh it per batch — the procedure and the reasoning live in `TranslationRepairTask.md` → **Refreshing `origin_chapter`**. Treat an un-backfilled range pull as a floor, never a census.

⚠️ **The entity pull is structurally biased toward *named* referents.** That's all an entity DB can hold. If a book's footnote list was built this way it will look like nothing but proper nouns — that is an artifact of the method, not the standard. Bare quotations, parodied proverbs and idioms with a specific source are equally in scope, and the entity pull is blind to every one of them.

### 2. The source-text scan — `footnote_scan.py`

```
python3 footnote_scan.py -b <id> --chapters 1-100      # collect (note: -b, not --book-id)
python3 footnote_scan.py -b <id> --report --chapters 1-100
python3 footnote_scan.py -b <id> --review              # TUI: SPACE keep / x reject
python3 footnote_scan.py -b <id> --export map.json     # feeds add_footnotes.py
```

An LLM reads each chapter's **source** text and proposes referents. Candidates land in the **main DB** (`footnote_candidates` / `footnote_scans`, migration 16). It is a *collector*: it never touches chapters, entities, or the real `footnotes` table, and applying an approved candidate stays a separate manual step (`add_footnotes.py`). Scan the whole book once; then triage a range at a time.

Review either in the `--review` TUI or in the **Footnotes admin GUI** (`/api/books/{id}/footnote-candidates`, pages `FootnoteCandidates.jsx` / `FootnoteReview.jsx`) — same queue, same rows.

Useful flags beyond the four above: `--workers N` / `--delay 5s` to pace a big backlog sweep, `--force` to re-scan chapters already scanned, and `--prune-unverified` to re-check stored candidates against the chapter source and drop the ones that aren't actually in it (honours `--dry-run`).

#### The scan may already be running

`modules/footnote_scan_module.py` scans **every newly saved chapter** on ingest and files candidates into the same queue. It **auto-enables for any book whose `source_language` is `zh`** (per-book override available in the module settings), so for an actively-translating Chinese book the backlog is usually already accumulating — check `--report` before launching a full sweep.

Two consequences:
- **Enabling it triggers no backfill, deliberately.** Chapters translated *before* the module was on still need one CLI sweep (`footnote_scan.py -b N --chapters 1-<frontier>`).
- The on-ingest scan quotes the book's **BOOK-SPECIFIC NOTES** into the scan prompt, so it judges referents against the English this translation actually uses. A bulk CLI sweep is the same core — keep the book notes current and both paths improve.

### ⭐ Why the scan is not optional

**A poem line spoken in dialogue never becomes an entity**, so an entity-driven pass is blind to it *by construction* — no amount of care compensates.

Book 14 is the cautionary case. The MC systematically plagiarizes the Chinese canon; we had footnoted exactly two borrowings (Su Shi, Du Mu) — and those two only because they happened to be entities. The scan found **fourteen more** bare in dialogue: Li Bai ×2, Lu You, Cen Shen, Wang Changling, Mencius, the Analects, the *I Ching*, Zhang Zai, Zhu Xi, the *Zuo Commentary*, the *Book of Songs*, and a Lü Dongbin quatrain that supplies a **chapter title**. Without them the book's whole conceit is invisible: the reader just thinks the MC is a genius.

Run both. They catch different things.

---

## Triage: three questions

Ask these in order. A candidate must pass all three.

**1. Is there a specific, identifiable source or referent?**
A named myth, deity, work, person, historical practice, real institution, brand, or a proverb/quotation traceable to a text. If you can't name what it points *at*, it's not a footnote.
- ✅ 刍狗 → the Daodejing. 唐僧肉 → *Journey to the West*. 阳澄湖洗澡蟹 → the crab-laundering scandal.
- ❌ 飞蛾扑火, 云泥之别, 中流砥柱 — generic chengyu that point at nothing but themselves.

**2. Is the point invisible in our English?**
Read the actual translated line. If an English reader gets the full meaning without help, a footnote only adds trivia.
- ✅ "slippers-on-backwards welcome", "Steamed goose heart!", "he's certainly a 'material'" — baffling as they stand.
- ❌ 沧海一粟 → "a drop in the ocean"; 青梅竹马 → "childhood sweethearts"; 山寨 → "knockoffs"; 东山再起 → "make a comeback". All perfectly clear English. The footnote would teach the reader about Su Shi or Li Bai without helping them *read the sentence*. **Reject.**

**3. Is it Chinese?**
Non-Chinese referents are out of scope even when the source treats them as memes: Bleach's Espada, Gundam, Naruto, One Piece, Neon Genesis Evangelion, Pokémon, *Game of Thrones*, "Make X Great Again", the Nobel Prize, the Oscars, La Fontaine's chestnuts. The English reader either recognizes them or is meant not to.

### The highest-value class: twisted idioms and parodied quotations

If a book has a systematic joke, the parodies *are* the book, and they are invisible without notes. Hunt them deliberately. Book 15's endgame is a labor-rights satire that puns the classical canon:

- 先斩后奏 ("behead first, report later" — an official's delegated authority) → **先涨后奏**, "raise wages first, report later."
- Mencius's 民为贵，社稷次之，君为轻 → "immortals are the most precious, sects come second, Heavenly Sovereigns are the lightest."
- 抱薪救火 ("carry firewood to quench a fire") punned on 薪 = *firewood* **and** *wages*.
- Zhang Zai's 为万世开太平 ("peace for all generations") → a campaign for higher pay.
- 兵贵神速 ("in war, speed is precious") → 钱贵神速, "money is precious and speed is divine."

Each reads as mildly odd phrasing in English. Footnoted, they're the thesis.

---

## What to footnote

China-specific referents the average English reader won't have:

- **Classical works & quotations** — the Four Great Novels, Jin Yong wuxia, Pu Songling, the Confucian canon. **Especially bare quotations** the MC passes off as his own (the 文抄公 trope). A quotation needs no proper noun to qualify: 窈窕淑女，君子好逑 earns a footnote about "Guan Ju" (关雎) from the Book of Songs.
- **Myth / religion / deities** — the Three Pure Ones, Kunpeng, Nüwa, the Golden Crow, Mount Buzhou, the Yellow Springs, Fengdu.
- **Daoist / Buddhist practice** — bigu (辟谷), embryonic breathing (胎息), 急急如律令, the Five Thunders, śarīra.
- **Folk custom & institutions** — 打生桩 (a living person buried under a foundation), 生祠, 冥币, the imperial exam ladder, 户口, 徭役.
- **Modern *Chinese* pop culture and institutions** — films, dramas, games, brands, real scandals, internet slang **with a specific referent**: Pinduoduo's 砍一刀, Ele.me parodied as "Are-You-Dead-Yet", Yu'e Bao as "Mana Treasure", the gutter-oil and tofu-dreg scandals, 996, 韭菜, the "No Chinese or dogs" sign, Jack Ma + Pony Ma fused into "Ma Yunteng".

## What NOT to footnote

- **Generic chengyu and slang with no source.** This is the single biggest reject category — roughly half of all scan output. 躺平, 摆烂, 牛马, 摸鱼, PUA, 学霸, 铁公鸡, 玉树临风, 囫囵吞枣.
- **Anything the English already self-glosses** (question 2 above).
- Genre cultivation jargon (Golden Core, tribulation, qi deviation) — the xianxia reader knows it.
- Proper names of in-world people, sects and places.
- Globally-known concepts (yin/yang, Taiji, Shaolin, Confucius, the Great Wall).
- **Non-Chinese referents.**
- An identity the narrative is deliberately **withholding**. But if a name simply never appears, an allusion-footnote is safe — anchor it on a non-spoiler reference.

---

## Verify before writing — the coincidence trap

**Read the source context for every candidate.** The classifier's label, and your own first impression, are both unreliable:

- **胡汉三** looked like the *Sparkling Red Star* meme. The source shows a bit-part villager who merely shares the name. The footnote was **deleted**.
- **北邙** looked like the famous burial mountain. In this book it's a *kingdom* (北邙国主 = its ruler). No footnote.
- **乐沐岚 (Le Mulan)** looks like Hua Mulan. It's just a character's name. The scanner even said so in its own body text — and still proposed it.

A footnote that glosses a coincidence is worse than no footnote.

---

## ⭐ The anchor pass — the real filter

`footnote_scan.py` reads the **source**, so its `term_en` is a *model guess* at our English. It routinely won't match our published wording. **Every surviving candidate must be located in the actual translated text before you write a word of its body.**

This pass kills more good candidates than triage does. In book 15 it removed a dozen-plus fine classical allusions per range, because **the translation model had paraphrased the referent out of the English**:

| Source | Our English | Verdict |
|---|---|---|
| 邯郸学步 | "like a clumsy imitator" | no anchor → drop |
| 登堂入室 | "accomplished" | no anchor → drop |
| 小巫见大巫 | "paled in comparison" | no anchor → drop |
| 只争朝夕 (Mao) | "fight for every moment" | no anchor → drop |
| 一夫当关万夫莫开 (Li Bai) | paraphrased away | no anchor → drop |
| 胯下之辱 (Han Xin) | paraphrased away | no anchor → drop |

**If the referent was paraphrased away, there is nothing on the page to annotate — drop it.** Don't invent an anchor, and don't rewrite the prose to make room; that's translation repair, a different workflow. *(Exception: if a truly load-bearing line is lost, surface it rather than silently dropping it — the user may choose to edit the prose to rescue it.)*

⚠️ **Do NOT filter candidates on "term_en appears in the prose."** That measures whether the model guessed our wording, not whether the candidate is good — it wrongly discards most of the list. Find the *real* anchor instead:

1. Locate `term_zh` in `untranslated`.
2. **The source and translated line arrays are NOT aligned** (translation merges paragraphs — e.g. 201 source lines → 158 English). Take the *proportional* index `int(i / len(src) * len(tl))` and read a ±3-line window.
3. Fall back to a keyword search of the translated chapter.
4. Read the **full matching line** before believing a hit (see false-positive greps below).

### Substring greps lie — always print the whole line

Every one of these bit during the book-15 pass:

| Grep | Falsely matched |
|---|---|
| `urn` | ret**urn**ed |
| `rap` | **rap**idly |
| `ant` | inst**ant** |
| `troll` | con**troll**ing, s**troll**ed |
| `still` | ordinary "still" |

Confirm the keyword is the *referent*, not a coincidental substring, before you anchor on it.

---

## Writing the body

**Style:** `Term (中文): brief gloss.` One to two sentences, factual, spoiler-free.

- For a quoted poem, **identify the poet and work; do not re-quote it.** The line is already on the page — the footnote's job is to say whose it is and why it matters.
- For a **parody**, state the original and name the swap. That is the entire value of the note: *"A pun on 先斩后奏, 'behead first, report to the throne later' — the standing authority to act and inform the emperor afterwards. Here 斩 ('behead') is swapped for 涨 ('raise wages')."*
- For modern cross-refs, note the MC recalls it from his past life where relevant.
- When we *translate* a parody/brand name rather than transliterate it, the body **must** include the original romanization, or the parody is invisible (e.g. ch11's "Along the River During the Deep Heavens Festival" needs *Qingming*; "Mana Treasure" needs *Yu'e Bao*).

---

## Anchoring

The `footnotes` table is the source of truth; the inline `[n]` marker and the trailing `[n] body` block are a **derived rendering, re-applied on every `save_chapter`**. So placement logic must live in `footnotes.py`, not in a script — anything else is lost on retranslation.

### Placement rules

- **First occurrence wins, book-wide.** A term may anchor in an earlier, already-reviewed chapter — that's correct, it's still the first mention. (In book 15 this *fixed* a triage miss: "leeks" anchored at ch22, a candidate an earlier batch had wrongly rejected.)
- ⚠️ **Common-word anchors will land on a coincidence.** "human skin" first occurs book-wide at ch36 in the unrelated idiom "a beast in human skin" — book-wide placement would have put the Pu Songling *Painted Skin* footnote there. **Eyeball the first-mention line whenever the anchor is an ordinary English phrase**, and force placement if needed:
  ```
  python3 add_footnotes.py --book-id N --chapter 457 --term "human skin" --footnote "..."
  ```
- ⚠️ **The chapter heading is `content[0]`.** If the term also appears in the chapter title, `add_footnotes.py` will happily anchor there and put a marker *inside the chapter heading*. Footnotes live in prose. Anchor on a body-only phrase instead ("selected as a Model Worker", "called the Blessing Sarira"). **Audit after every batch** — scan content lines matching `^Chapter ` for a `[\d+]` marker.
- **A referent that appears ONLY in a chapter title gets no footnote.** (Xiang Yu's "walking in brocade by night", "money can reach the gods".) Flag it for the repair pass if it's worth rescuing.
- **Brackets and quotes are handled automatically.** `footnotes.marker_position()` puts the marker *outside* a matching pair: anchor `Rhapsody on the Epang Palace` and it renders `《Rhapsody on the Epang Palace》[2]`. Handles `《》「」『』〈〉（）〔〕 "" '' () [] {}` and nests them. **Never bake a closing bracket into the anchor.** Ordinary punctuation does *not* hop, so `"Decree Extending Grace[2]."` correctly keeps its marker before the period. Anchor the **whole** title — a partial anchor ends mid-title with no closer to hop.
- ⚠️ **Plurals are NOT automatic.** Anchoring `Licentiate` against "Licentiates" yields `Licentiate[2]s`. Anchor the plural the prose actually uses ("Yellow Turban Strongmen").
- **Case-sensitive, exact substring**, including punctuation *inside* quotes: `he's certainly a 'material'` will NOT match `he's certainly a 'material.'` — the period is inside the quote.
- **Avoid short/common anchors** that collide (bare `Gu` matches "Guard"), and avoid anchoring on a **simile** rather than the literal thing.
- Markers auto-renumber by reading order, so a new note above an existing one correctly becomes `[1]`.

### Don't re-gloss across ranges

`add_footnotes.py` is idempotent **only on an exact body match**. A reworded gloss of a referent you already footnoted sails straight through as a duplicate. Recurring referents in book 15 that had to be caught by hand: 财神, 洗髓经, 人上人, 饕餮, 昆仑, 齐天大圣, 蛊, 三清, 神霄, 补天. **Check the running list before writing a body.**

---

## Tools

| Script | Purpose |
|---|---|
| `footnote_scan.py -b N` | LLM-scan the source for candidates → `footnote_candidates` in the main DB; `--report`, `--review` TUI, `--export`, `--prune-unverified`, `--workers`/`--delay`, `--force`. **Takes `-b`, not `--book-id`.** |
| `add_footnotes.py --book-id N --file map.json` | Add first-mention footnotes. `--dry-run` first. `--chapter N` forces placement. `--term`/`--footnote` for a one-off |
| `list_footnotes.py --book-id N` | List rendered footnotes with their sentence; `--orphans`, `--reanchor`, `--format json` |
| `delete_footnote.py --book-id N --chapter M` | Remove a footnote + its rendered marker/def. `--number`/`--anchor`/`--body-contains`/`--id`/`--all`; dry-run by default, `--apply` commits |

`add_footnotes.py` is **idempotent** on body text — safe to re-run as more chapters are translated.

To fix a bad placement: `delete_footnote.py` then re-add with a better anchor.

### DB gotcha

`db.get_chapter()`'s signature is `get_chapter(chapter_id=None, book_id=None, chapter_number=None)` — **`chapter_id` is first**. `db.get_chapter(15, 3)` silently reads a chapter from *a different book*. Always use keyword args: `db.get_chapter(book_id=15, chapter_number=3)`. Source lines come back under `untranslated`, translated lines under `content`. Symptom of getting this wrong: `list_chapters()` and `get_chapter()` disagree about a chapter's title.

---

## Workflow

0. **Before the first pull** — make sure `origin_chapter` has been refreshed for this batch (owned by `TranslationRepairTask.md` → **Refreshing `origin_chapter`**), or the entity pull's `--origin-chapter` ranges will be incomplete. Then `footnote_scan.py -b N --report` to see what the on-ingest module has already collected, and sweep only the chapters it missed.
1. **Collect** — entity pull for the batch, plus `footnote_scan.py --report`.
2. **Triage** with the three questions. Most candidates die here.
3. **Verify** the source context of every survivor (coincidence trap).
4. **Anchor** in the *translated* text; drop the paraphrased-away and the title-only. Print full lines — greps lie.
5. **Dedupe** against footnotes already placed in earlier ranges.
6. **Present** the curated set for approval (`AskUserQuestion`, grouped strong vs optional).
7. **Verify anchors programmatically** — for each term, confirm the book-wide first occurrence is the chapter you intend, and eyeball any that land earlier than expected.
8. **Dry-run**, then apply.
9. **Audit**: rendered `↳` lines from `list_footnotes.py` (the plural artifact is invisible in a dry-run); no markers in `^Chapter ` heading lines; no chapter over 4.
   - A `↳` can *look* mangled when the sentence-extractor splits on `?`, `!` or a quote. Check the raw chapter line before "fixing" a non-problem.

**File hygiene:** maps live in `/tmp`, named `footnotes_b<id>_<range>.json`.

**Note:** `--substitute`-style chapter rewrites do not re-render EPUB/HTML exports — re-export downstream if needed. A translation fix that changes an anchor's text will **orphan** its footnote: delete and re-add, or `list_footnotes.py --reanchor`.

---

## Expected yield

About **28%** of scan candidates survive, but it varies by arc, and that's information, not noise:

- **Early school/satire chapters** run low (~8%) — the register is internet slang, which mostly has no source to point at.
- **Mid-book wuxia/Daoist/exam arcs** run high (~30–37%) — dense with real classics.
- **Late-middle chapters sag** (~20%) — the scanner falls back on plain chengyu.
- **The climax spikes** — a book with a systematic joke saves its best parodies for the end.

Don't force a quota. A range that honestly yields 12 footnotes is a good range.
