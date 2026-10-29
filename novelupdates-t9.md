# NovelUpdates from T9: integration guide

For whoever builds the T9 side of NovelUpdates (NU) posting. The scraper
(this repo) now has the browser half. T9 owns the logic. Read this first,
then the live API docs at `GET /docs` on the scraper.

Status as of 2026-10-29:
- **Verified live:** login, search, group sync, the duplicate check, dry
  runs of both forms, and one real add-release (c2 of *The Law-Abiding
  Necromancer*, which NU confirmed).
- **Never actually submitted:** add-series. Read
  [Open issues](#open-issues) before relying on its result.

## The split

| Scraper (`/nu/*`) | T9 |
|---|---|
| Logs in, and logs in again when the session lapses | Decides *what* to post and *when* |
| Searches NU by untranslated title | Keeps the per-series NU state (below) |
| Reads our group page's series list | Waits out NU moderation (~24 h after a first release) |
| Fills and submits the add-release and add-series forms | Posts releases in order, one at a time |
| Refuses to post the same release twice | Supplies the English metadata for new series |
| Keeps screenshots and a log of submissions | Asks a human when the scraper says "check NU" |

The scraper never decides anything on a timer. Every operation is one
request from T9.

## Calling it

Use the scraper's Python client. **T9's copy (`~/translate/scraper_client.py`)
is out of date.** Copy `client/scraper_client.py` from this repo over it: the
new version adds `nu_search`, `nu_sync_group`, `nu_group_series`,
`nu_release`, `nu_add_series` and `nu_submissions`. All of them block until
the job is done, and raise `ScraperError` if it fails. The base URL and token
are the ones `scrape_sync.py` already uses.

Every `POST /nu/*` queues a job on the scraper's **single worker**, the same
queue as T9's scrape jobs (`nu` jobs are normal priority, so they run ahead
of low-priority backfills):

1. `POST` returns `202 {id, status: "queued", ...}`. If an identical request
   is already queued or running, it returns `200` with that job and
   `deduped: true`.
2. Poll `GET /jobs/:id` until `status` is `done`, `failed` or `cancelled`.
   `blocked` means a Cloudflare challenge is waiting for a human over noVNC;
   just keep polling.
3. `GET /jobs/:id/result` returns the operation's result JSON. A failed job
   has no result (409); the reason is in the job's `error` field.

Timing: each page load waits about 3 s (NU's pacing setting), and every
operation loads 1–5 pages. A search takes about 10–30 s and a release about
20–40 s. Follow `scrape_sync.py`'s pattern and keep **one NU job in flight at
a time**: submit the next only after the last one finishes. Jobs run in the
order they were queued, but waiting also stops one failure from cascading.

## Operations

### `POST /nu/search {names}`: is this series on NU?
`names` (1–6 strings) are the untranslated title in **every form T9 has**,
Traditional and Simplified both. NU stores whichever form the submitter
typed, and its search doesn't convert. Live example: `我，死靈法師，遵紀守法`
finds nothing, while `我，死灵法师，遵纪守法` finds *The Law-Abiding Necromancer*.
Use `trad_simp.py` to make the other form.

```json
{ "names": ["…", "…"],
  "found": true,
  "series": { "title": "The Law-Abiding Necromancer",
              "url": "https://www.novelupdates.com/series/the-law-abiding-necromancer/",
              "slug": "the-law-abiding-necromancer", "id": "232967" },
  "inOurGroup": true,
  "candidates": [ { "slug": "…", "title": "…", "url": "…", "id": "…",
                    "associatedNames": ["…"], "exact": true } ] }
```
- `found` is true only when the series page's title or one of its
  associated names equals one of `names`. The comparison ignores spaces,
  punctuation and full-width forms, but doesn't convert between
  Traditional and Simplified.
- `found: false` with an empty `candidates` list means the series is
  **new** to NU.
- `found: false` with candidates means the search returned something, but
  nothing matched exactly. **Don't treat that as new.** Show the candidates
  to a human, who either confirms the series is new or picks the right one;
  then store its slug. Only the first 5 candidates are opened to compare
  names; they're the ones with `associatedNames` and `exact` filled in.
- `inOurGroup` is read from the stored group list (see the next section),
  so run a sync first if the list may be stale.
- `series.title` is the series' real NU name. Store `slug` and `url`; every
  later call uses them.

### `POST /nu/group/sync {}` and `GET /nu/group/series`: our group's series
The sync re-reads our group page and returns `{group, syncedAt, added:
[slug], series: [{slug, title, url, firstSeenAt, lastSeenAt}]}`. `added`
lists slugs never seen before. The GET returns the stored list instantly,
without opening the browser.

NU adds a series to our group page **only after it approves our first
release of it**, which takes about a day. That is how T9 learns a series is
"active" (see the procedure below).

### `POST /nu/releases {series, release, link, date?, dryRun?, force?}`: post a release
- `series`: the NU series URL or slug.
- `release`: NU's label. Use the same style as our existing releases, which
  are plain `c<N>` (`c745`, `c1016`); NU's example is `v2c10`. NU shows the
  label as typed, so keep it consistent. Labels are compared ignoring case
  and spaces.
- `link`: the chapter's public URL on our reader site.
- `date`: `MM/DD/YYYY`, only for a release made before today. Leave it out
  otherwise.
- `dryRun: true`: fill the form and screenshot it, but don't submit. Use it
  for development.
- `force: true`: see the next list.

Result `status` is one of:

| status | meaning | T9 does |
|---|---|---|
| `submitted` | NU confirmed it; `message` is NU's thank-you, `reply` the page text, `screenshots` the filenames | Mark it sent. It shows on NU after moderation |
| `exists` | Our group already has this label on the series page; nothing was posted | Mark it sent |
| `already_submitted` | The scraper's log says it was sent before (`submittedAt`, `jobId`); nothing was posted | Mark it sent |
| `dry_run` | Form filled, not submitted; `form` holds the values | Nothing |

The duplicate guard has two layers. The series page check only reads the
first page of the release table (the newest releases). The local log covers
the hours when NU's moderation hides a new release. T9 should still track
what it has sent.

Errors (the job fails; the message is in `error`):

| error contains | cause | T9 does |
|---|---|---|
| `may have gone through` / `no reply from NovelUpdates` | It clicked submit but never saw the reply (crash, timeout) | **Stop that series** and ask a human to check NU. Resend with `force: true` only if the release isn't there |
| `neither confirmed nor refused` | NU's reply had no thank-you and no error | **Stop that series** and ask a human to check NU, as above |
| `NovelUpdates refused the release` | NU's reply had new error wording; the attempt is logged as failed | Show it to a human; a plain retry is allowed |
| `didn't offer id` | The add-release form's series search didn't list this series (e.g. it's still pending approval) | Wait and retry later, or escalate |
| `no series at` | Bad slug | Fix the stored slug |
| `NovelUpdates login failed` / `must be set` | Credentials or config on the scraper | Escalate (scraper `.env`) |
| `Cloudflare blocked` | Firewall block page, can't be solved | Retry later; escalate if it keeps happening |
| `challenge not solved within` | Nobody solved a challenge over noVNC in time | Retry later |

### `POST /nu/series {...}`: submit a new series
Search first; this endpoint doesn't. Required: `title` (English name),
`associatedNames` (**raw title first**, which NU requires; include both the
Traditional and Simplified forms so later searches find it) and `genres`
(at least one). Optional: `description`, `type` (`Light Novel` |
`Published Novel` | `Web Novel`), `tags`, `language` (e.g. `Chinese`),
`authors`, `artists`, `year`, `statusInCoo` (e.g. `["1200 Chapters
(Ongoing)"]`), `licensed`, `completelyTranslated`, `originalPublishers`,
`englishPublishers`, `coverUrl` (JPEG, PNG, GIF or WebP; the scraper
downloads and uploads it), `dryRun`, `force`.

Names must be NU's own spellings. Before anything is sent, the job fails
with `NovelUpdates doesn't know: genres X; tags Y` for any name it doesn't
recognise. Genres: Action, Adult, Adventure, Comedy, Drama, Ecchi, Fantasy,
Gender Bender, Harem, Historical, Horror, Josei, Martial Arts, Mature, Mecha,
Mystery, Psychological, Romance, School Life, Sci-fi, Seinen, Shoujo, Shoujo
Ai, Shounen, Shounen Ai, Slice of Life, Smut, Sports, Supernatural, Tragedy,
Wuxia, Xianxia, Xuanhuan, Yaoi, Yuri. There are 772 tags (exact names,
e.g. "Ability Steal"); T9's `tag_vocab.json` may need mapping onto them.

`nu_vocabulary()` (`POST /nu/vocabulary`) returns the current lists,
`{fetchedAt, genres, tags, types, languages}`, each a list of `{id, name}`.
NU sends them all with the add-series page (the tag box only filters a
preloaded list, with no ajax), so one call gets everything. Cache the
result in T9 and refresh it now and then (weekly is plenty). Map T9's
genres and tags onto these names before calling `nu_add_series()`.

Result `status` is `submitted`, `already_submitted` (the log is keyed on the
normalized raw title) or `dry_run`. Errors are as for releases.

### Also available
`POST /nu/vocabulary` is described under add-series. `POST /nu/login` checks or refreshes the login (other operations log in by
themselves). `GET /nu/submissions?limit=` lists everything sent, newest
first. `GET /nu/screenshots/:file` serves screenshots, which are kept 24 h.

## The procedure T9 implements

Keep a state per T9 book that we publish on NU:

```
untracked → searching → linked ──────────────→ first_release_sent → active
                  │      (found, inOurGroup)            ▲  (slug shows up in group sync)
                  │                                     │
                  ├→ needs_review (candidates, no exact match: a human picks)
                  └→ new_series → series_submitted ─────┘ (see open issue 2)
```

Suggested fields: `nu_state`, `nu_slug`, `nu_url`, `nu_title`,
`nu_first_release_at`, `nu_last_release` (label), `nu_checked_at`, plus a
per-chapter "sent to NU" flag or label.

1. **Find it.** `nu_search` with both title forms.
   - `found` and `inOurGroup` → `active`.
   - `found`, not in our group → `linked`: NU has the series, but we've never
     posted to it.
   - nothing found → `new_series`.
   - near misses → `needs_review`.
2. **First release (linked).** Post only the **first chapter we publish**,
   then wait. Record the time (`first_release_sent`).
3. **Wait for approval.** About 20 h after the first release, run
   `nu_sync_group()` every few hours. When the slug appears in the stored
   list (`nu_group_series()`), the series is `active`. If it hasn't appeared
   after about 72 h, alert a human: moderation may have rejected it.
4. **Active.** Post every published chapter that hasn't been sent yet,
   oldest first, one job at a time, with a short pause between posts.
   (NU's rate limits are unknown; our group page already lists well over
   1,000 releases, all posted by hand.)
   Chapter numbers can have gaps; post what exists and never invent
   labels.
5. **New series.** Check the metadata against the name lists above, dry-run
   it, then `nu_add_series()`. The user wanted new series to be approved by
   a human before submitting; build that gate in T9. Then follow step 2 for
   its first release. See open issue 2.

Notifications: the scraper sends ntfy notices only for challenges. Alerts
for `needs_review`, "check NU" and approval timeouts are T9's job.

## Open issues

1. **Add-release is verified with one real submit; add-series isn't.**
   On 2026-10-29, c2 of *The Law-Abiding Necromancer* was submitted. NU
   reloads `/add-release/` with every field still filled in and adds one
   line: "Thank you! Your submission should be approved within 24 hours."
   The scraper now counts a submit as `submitted` **only** when that
   confirmation appears, and returns it as `message`. If the reply has
   neither the confirmation nor an error, the job fails with "neither
   confirmed nor refused", the attempt stays marked as possibly sent, and a
   human checks NU. NU's reply after add-series hasn't been seen yet. If it
   uses different wording, the first real series submit ends in that human
   check; the scraper side then gets updated to recognise it.
2. **The first release of a brand-new series.** The add-series page says:
   *"Before your series is approved, you must add one release for the series
   you're submitting using this link: Add Release."* A pending series has
   no public `/series/<slug>/` page, so `POST /nu/releases` can't post to it
   yet: it needs the series page for the id and the duplicate check. The
   scraper side will be extended once we see the real add-series reply
   (likely by picking up that link and posting through it). Until then, a
   new series' first release is done by hand.
3. **Only the first page of a series' release table is checked** for
   duplicates. That's enough for newest-first posting. Re-posting an old
   chapter relies on T9's records and the scraper's log.

## Where things live

- Scraper code: `src/nu.js` (operations), `src/sites/novelupdates.js`
  (in-page readers and form helpers), routes in `src/server.js`, and the
  `nu_group_series` and `nu_submissions` tables in `src/db.js`. The README
  has a NovelUpdates section.
- Saved NU pages (local only, gitignored): `~/scraper/test/fixtures/nu/`.
- T9: `scrape_sync.py` (the lane pattern to copy), `scraper_client.py` (to
  replace), `trad_simp.py` (OpenCC).
