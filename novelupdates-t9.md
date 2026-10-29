# NovelUpdates from T9: integration guide

For whoever builds the T9 side of NovelUpdates (NU) posting. The scraper
(this repo) now has the browser half. T9 owns the logic. Read this first,
then the live API docs at `GET /docs` on the scraper.

Status as of 2026-10-29: everything below has run live. By 07:10 that day
the scraper had submitted 102 releases and 2 new series. One new series had
its first chapter posted by id while still pending. Associated names were
edited once for real: the Traditional raw title was added to *The
Law-Abiding Necromancer*, after which `nu_search` finds it by that form
alone.

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

Use the scraper's Python client. **T9's copy (`scraper_client.py` at the root of the T9 repo)
is out of date.** Copy `client/scraper_client.py` from this repo over it: the
new version adds `nu_search`, `nu_sync_group`, `nu_group_series`,
`nu_release`, `nu_releases`, `nu_add_series` and `nu_submissions`. All of them block until
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
operation loads 1–4 pages. A search takes about 10–30 s and a release about
10–15 s. When several chapters of one series are due, post them with one
`POST /nu/releases/batch` rather than one job each: after the first, each
release costs one paced submit (about 6 s), and T9 polls once for the lot. Follow `scrape_sync.py`'s pattern and keep **one NU job in flight at
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

### `POST /nu/release-search {title}`: find a series in the add-release form
Types `title` (the series' **English** NU name) into `/add-release/`'s Series
box and returns what it offers. Nothing is submitted. That box searches NU's
whole database, **including series still awaiting approval**. Those have no
public page, so `POST /nu/search` can't see them. This is how T9 gets the id
of a series it just submitted.

```json
{ "title": "The Law-Abiding Necromancer",
  "match": { "id": "232967", "title": "The Law-Abiding Necromancer" },
  "ambiguous": false,
  "hits": [ { "id": "232967", "title": "The Law-Abiding Necromancer" } ] }
```
- `match` is set only when exactly one hit's title equals `title`, ignoring
  case, spaces and punctuation. Long titles come back in full.
- `ambiguous` means several hits share that exact title. A human picks one.
- `match: null` with hits means the search returned only near misses. A
  partial title such as "Law-Abiding" lists every series containing it.
- **Store `match.id`.** Post to the series by id from then on, not by name.

### `POST /nu/releases {series | seriesId+seriesTitle, release, link, date?, dryRun?, force?}`: post a release
- Name the series in one of two ways (not both):
  - `series`: the NU series URL or slug, for an approved series. The
    scraper reads its page for the id and checks the release table there.
  - `seriesId` + `seriesTitle`: from `POST /nu/release-search`, for a
    series still awaiting approval. The title is what gets typed into the
    Series box, and the submit only goes ahead if the box offers that exact
    id; an id paired with the wrong title is refused. There's no page to
    check, so the scraper's log is the only duplicate guard. Once the
    series is approved, `series` checks the log under both its slug and its
    id, so a first chapter sent by id is still recognised.
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
| `didn't offer id` | The Series box didn't offer this id for that title (wrong `seriesTitle`, or the series was renamed or removed) | Run `POST /nu/release-search` again and check the id; escalate |
| `no series at` | Bad slug | Fix the stored slug |
| `NovelUpdates login failed` / `must be set` | Credentials or config on the scraper | Escalate (scraper `.env`) |
| `Cloudflare blocked` | Firewall block page, can't be solved | Retry later; escalate if it keeps happening |
| `challenge not solved within` | Nobody solved a challenge over noVNC in time | Retry later |

### `POST /nu/releases/batch {series | seriesId+seriesTitle, releases: [{release, link, date?}], dryRun?, force?}`: post several releases
The same as `POST /nu/releases` for up to 50 releases of **one** series, in
the order given: **oldest first**, as NU asks. The series page and the form
are loaded once; NU's reply to each submit is the form with the series and
group still picked, so each further release is refilled and submitted on it.
Submits are spaced like page loads. Every release gets its own `filled` and
`submitted` screenshots (`nu-<job>-<n>-release-....png`, `n` counting from 1).

Result: `{status, series: {title, slug?, id}, submitted, releases}`, with one
`releases` entry per release sent, in order: `{release, link, status, ...}`.
Each entry's `status` and fields are as in the table above (an `exists`
entry's table row is `live`), plus:

| status | meaning | T9 does |
|---|---|---|
| `failed` | This release failed; `error` is the message the single endpoint would have failed with | As the error table below says for that message |
| `not_attempted` | An earlier release failed, so this one wasn't tried | Send it again later, in a new batch, once the failure is dealt with |

The batch's `status` is `stopped` if an entry failed, else `done`. The job
itself fails (no result) only for a problem before anything was posted:
login, a bad slug, a Cloudflare block. A `may have gone through` failure
still means **stop that series** until a human has checked NU.

A batch is deduplicated like any job only when it is identical. If the
scraper restarts mid-batch, resend the same list: the log answers
`already_submitted` for the releases that went out, and the one caught
mid-submit (if any) fails with `may have gone through`.

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

### `POST /nu/series-info {series}`: read a series page
Returns `{id, title, slug, url, associatedNames, releases}`. `releases` is
the first page of the release table (`{date, group, groupUrl, release}`,
newest first). No login is needed.

### `POST /nu/associated-names {series, add?, remove?, dryRun?}`: edit the names
Adds names to, or removes names from, a series' Associated Names. Use it,
for example, to add the Traditional raw title so `nu_search` finds the
series under both forms, or to add our English title.
- **It merges; it doesn't replace.** It reads the current list, drops
  `remove`, and appends each `add` that isn't there already. Other
  people's entries stay. Names compare ignoring case, spaces and
  punctuation, so re-adding a name changes nothing.
- It uses the series page's own Edit/Save, then **reloads the page to check**
  what NU kept.
- Result `status`:
  - `updated`, with `before`, `after`, `saved` and a `screenshot`.
  - `unchanged`: nothing to do.
  - `dry_run`: works out `after` without saving.
- The job fails if NU kept something other than what was sent
  (`didn't save as sent`, listing what's missing or unexpected), or if
  Save got no answer within 30 s. Either way, look at the series page.
- Every save is logged in `GET /nu/submissions` as kind `names`, with the
  lists from before and after.

### Also available
`POST /nu/vocabulary` is described under add-series. `POST /nu/login` checks or refreshes the login (other operations log in by
themselves). `GET /nu/submissions?limit=` lists everything sent, newest
first. `GET /nu/screenshots/:file` serves screenshots, which are kept 24 h (new-series
ones 7 days).

## The procedure T9 implements

Keep a state per T9 book that we publish on NU:

```
untracked → searching → linked ──────────────→ first_release_sent → active
                  │      (found, inOurGroup)            ▲  (slug shows up in group sync)
                  │                                     │
                  ├→ needs_review (candidates, no exact match: a human picks)
                  └→ new_series → series_submitted → (release-search finds the id) ─┘
```

Suggested fields: `nu_state`, `nu_id`, `nu_slug`, `nu_url`, `nu_title`,
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
   a human before submitting; build that gate in T9.
   - Then call `nu_release_search(title)` with the English title just
     submitted, and store `match.id` (`series_submitted`). If there's no
     match yet, retry a few times over a few minutes.
   - If it's still missing or `ambiguous`, ask a human.
   - Then post its first chapter with `nu_release(series_id=…,
     series_title=…)`. NU requires that first release before it approves
     the series, so do it promptly. From there, follow step 3.
   - Once the series is approved and has a slug, switch to posting by
     `series`.

Notifications: the scraper sends ntfy notices only for challenges. Alerts
for `needs_review`, "check NU" and approval timeouts are T9's job.

## Open issues

1. **Resolved: what counts as a confirmed submit.** NU answers add-release
   with "Thank you! Your submission should be approved within 24 hours."
   It answers add-series with "Thank you! Your submission is now pending."
   Both match the scraper's confirmation check and come back as `message`.
   A reply with neither still ends in the "neither confirmed nor refused"
   human check.
2. **Resolved: a pending series' first release.** `POST /nu/release-search`
   plus posting by `seriesId` has worked for real: `id:238334 c1` was
   posted a minute after that series was submitted.
3. **Only the first page of a series' release table is checked** for
   duplicates. That's enough for newest-first posting. Re-posting an old
   chapter relies on T9's records and the scraper's log.

## Where things live

- Scraper code: `src/nu.js` (operations), `src/sites/novelupdates.js`
  (in-page readers and form helpers), routes in `src/server.js`, and the
  `nu_group_series` and `nu_submissions` tables in `src/db.js`. The README
  has a NovelUpdates section.
- Saved NU pages: `test/fixtures/nu/` in the scraper checkout on the home box
  (gitignored, so not in the repo).
- T9: `scrape_sync.py` (the lane pattern to copy), `scraper_client.py` (to
  replace), `trad_simp.py` (OpenCC).
