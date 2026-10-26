"""Client for the chapter scraper API (stdlib only; Python 3.9+).

Copy this file into the translation pipeline. Typical flow:

    from scraper_client import ScraperClient

    sc = ScraperClient("http://100.65.58.103:8787", token=os.environ["SCRAPER_TOKEN"])

    # What's new? Read the TOC, then fetch everything after what we have.
    toc = sc.toc("https://twkan.com/book/82888.html")
    if toc["chapterCount"] > have:
        export = sc.chapters("https://twkan.com/book/82888.html", from_chapter=have + 1)
        for ch in export["chapters"]:
            ...  # ch["number"], ch["title"], ch["text"]

Both calls block until the job finishes (polling), raising ScraperError if it
fails. Use submit_* / wait / result for finer control. A job that stops on an
unsolved challenge fails; retry(job_id) resumes it where it left off.

Backfills (a whole new book) should be submitted with priority="low": they
run when nothing else is queued and step aside between pages for normal
jobs, so daily updates never wait behind them. Results can be read while a
job runs, in slices, so translation can start before a backfill finishes:

    job = sc.submit_chapters(book_url, priority="low")
    done = 0
    while True:
        st = sc.status(job["id"])
        for ch in sc.iter_chapters(job["id"], start=done):
            ...                      # only chapters not seen yet
            done += 1
        if st["status"] in ("done", "failed", "cancelled"):
            break
        time.sleep(60)

(The last chapter of a running job can still be growing if the site splits
it across pages; treat it as final once the job is done.)

Command line:
    python scraper_client.py toc <url>
    python scraper_client.py chapters <url> [--from N] [--to N] [--max N] [-o out.json]
    python scraper_client.py status <job-id> | result <job-id> | retry <job-id> | cancel <job-id>
  (reads SCRAPER_URL and SCRAPER_TOKEN from the environment)
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

FINAL = {"done", "failed", "cancelled"}


class ScraperError(Exception):
    def __init__(self, message: str, job: dict | None = None):
        super().__init__(message)
        self.job = job


class ScraperClient:
    def __init__(self, base_url: str, token: str = "", timeout: float = 30):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    # -- low level ---------------------------------------------------------
    def _req(self, method: str, path: str, body: dict | None = None) -> tuple[int, object]:
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(self.base_url + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as res:
                return res.status, json.load(res)
        except urllib.error.HTTPError as e:
            try:
                detail = json.load(e)
            except Exception:
                detail = {"error": e.reason}
            raise ScraperError(f"{method} {path}: HTTP {e.code}: {detail.get('error') or detail}") from None

    # -- jobs --------------------------------------------------------------
    def submit_chapters(self, url: str, from_chapter: int | None = None, to_chapter: int | None = None,
                        max_chapters: int | None = None, from_url: str | None = None,
                        delay_ms: int | None = None, priority: str | None = None,
                        engine: str | None = None) -> dict:
        """Queue a chapter scrape. priority="low" for backfills. Returns the
        job (an identical active job is returned instead, with deduped=True)."""
        body = {"url": url, "fromChapter": from_chapter, "toChapter": to_chapter,
                "maxChapters": max_chapters, "fromUrl": from_url, "delayMs": delay_ms,
                "priority": priority, "engine": engine}
        return self._req("POST", "/jobs", {k: v for k, v in body.items() if v is not None})[1]

    def submit_toc(self, url: str, engine: str | None = None) -> dict:
        """Queue a table-of-contents lookup."""
        return self._req("POST", "/toc", {"url": url, **({"engine": engine} if engine else {})})[1]

    def status(self, job_id: str) -> dict:
        return self._req("GET", f"/jobs/{job_id}")[1]

    def result(self, job_id: str, offset: int | None = None, limit: int | None = None) -> dict:
        """The job's export. With offset/limit, only that slice of chapters
        (chapterCount stays the total)."""
        q = "&".join(f"{k}={v}" for k, v in (("offset", offset), ("limit", limit)) if v is not None)
        return self._req("GET", f"/jobs/{job_id}/result" + (f"?{q}" if q else ""))[1]

    def iter_chapters(self, job_id: str, start: int = 0, page: int = 100):
        """Yield the job's chapters from index `start`, fetching `page` at a time."""
        offset = start
        while True:
            r = self.result(job_id, offset=offset, limit=page)
            yield from r["chapters"]
            offset += r["returned"]
            if r["returned"] < page or offset >= r["chapterCount"]:
                return

    def retry(self, job_id: str) -> dict:
        return self._req("POST", f"/jobs/{job_id}/retry")[1]

    def cancel(self, job_id: str) -> dict:
        return self._req("DELETE", f"/jobs/{job_id}")[1]

    def wait(self, job_id: str, poll: float = 15, timeout: float | None = None, on_update=None) -> dict:
        """Poll until the job finishes. Returns the final job. A blocked job
        (waiting on a human for a challenge) keeps waiting; the server fails
        it on its own challenge timeout."""
        start = time.monotonic()
        while True:
            job = self.status(job_id)
            if on_update:
                on_update(job)
            if job["status"] in FINAL:
                return job
            if timeout is not None and time.monotonic() - start > timeout:
                raise ScraperError(f"timed out waiting for job {job_id}", job)
            time.sleep(poll)

    # -- blocking conveniences ---------------------------------------------
    def chapters(self, url: str, poll: float = 15, timeout: float | None = None, on_update=None,
                 **kwargs) -> dict:
        """Scrape and return the export JSON. Raises ScraperError if the job
        fails or is cancelled (err.job holds it; its partial result is still
        available via result(job_id), and retry(job_id) resumes it)."""
        job = self.submit_chapters(url, **kwargs)
        job = self.wait(job["id"], poll=poll, timeout=timeout, on_update=on_update)
        if job["status"] != "done":
            raise ScraperError(f"job {job['id']} {job['status']}: {job.get('error', '')}", job)
        return self.result(job["id"])

    def toc(self, url: str, poll: float = 5, timeout: float | None = None) -> dict:
        """Read a book's TOC: chapterCount, latest, chapters[{position, number, title, url}]."""
        job = self.submit_toc(url)
        job = self.wait(job["id"], poll=poll, timeout=timeout)
        if job["status"] != "done":
            raise ScraperError(f"toc job {job['id']} {job['status']}: {job.get('error', '')}", job)
        return self.result(job["id"])


def _main(argv: list[str]) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Chapter scraper client")
    ap.add_argument("--url", default=os.environ.get("SCRAPER_URL", "http://100.65.58.103:8787"))
    ap.add_argument("--token", default=os.environ.get("SCRAPER_TOKEN", ""))
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("toc"); t.add_argument("book_url")
    c = sub.add_parser("chapters"); c.add_argument("book_url")
    c.add_argument("--from", dest="from_chapter", type=int); c.add_argument("--to", dest="to_chapter", type=int)
    c.add_argument("--max", dest="max_chapters", type=int); c.add_argument("-o", "--out")
    c.add_argument("--backfill", action="store_true", help="submit with priority=low")
    for name in ("status", "result", "retry", "cancel"):
        sub.add_parser(name).add_argument("job_id")
    a = ap.parse_args(argv)

    sc = ScraperClient(a.url, a.token)
    show = lambda j: print(f"  {j['status']:9} pages={j['progress'].get('pages', 0)} "
                           f"chapters={j['progress'].get('chapters', 0)} {j['progress'].get('lastTitle', '')}"
                           + (f"  BLOCKED, solve at {j['vncUrl']}" if j.get("vncUrl") else ""), file=sys.stderr)
    try:
        if a.cmd == "toc":
            r = sc.toc(a.book_url)
            print(f"{r['chapterCount']} chapters; latest: {r['latest']}", file=sys.stderr)
            out = r
        elif a.cmd == "chapters":
            out = sc.chapters(a.book_url, from_chapter=a.from_chapter, to_chapter=a.to_chapter,
                              max_chapters=a.max_chapters, priority="low" if a.backfill else None,
                              on_update=show)
            if a.out:
                with open(a.out, "w", encoding="utf-8") as f:
                    json.dump(out, f, ensure_ascii=False, indent=2)
                print(f"wrote {out['chapterCount']} chapters to {a.out}", file=sys.stderr)
                return 0
        else:
            out = getattr(sc, a.cmd)(a.job_id)
    except ScraperError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    json.dump(out, sys.stdout, ensure_ascii=False, indent=2)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
