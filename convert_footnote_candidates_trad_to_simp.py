#!/usr/bin/env python3
"""Convert a book's stored footnote candidates to simplified Chinese.

Needed whenever a book's source is converted trad->simp AFTER its chapters were
scanned for footnote candidates: the rows keep traditional `term_zh` while the
chapter source is now simplified, so the referent no longer matches the page.

That breaks two things:
  * `footnote_scan.py --prune-unverified` deletes the stale rows as
    "fabrications" -- and does so UNEVENLY, because candidate_in_source()
    tolerates a one-character difference (清明節 -> 清明节 survives by luck,
    蒼蠅館子 -> 苍蝇馆子 does not).
  * every downstream anchor script has to remember to convert on the fly.

Converts term_zh, sentence, body, chapter_title and term_en via
`trad_simp.convert_text` -- the SAME guarded converter used on the chapters, not
bare OpenCC. This matters: plain `t2s` leaves every 著 unconverted, so a bare
OpenCC pass would leave aspect-particle 著 traditional and STILL not match the
converted source. Conversion is idempotent, so re-running is safe.

Reports how many candidates verify against their chapter source before and
after, which is the real success metric -- once "after" is 100%,
`--prune-unverified` is safe to run again.

Dry-run by default; pass --apply to write.

Usage:
    python3 convert_footnote_candidates_trad_to_simp.py --book-id 82
    python3 convert_footnote_candidates_trad_to_simp.py --book-id 82 --apply
"""
import argparse
import warnings

warnings.filterwarnings("ignore", category=FutureWarning)

from config import TranslationConfig
from db import DatabaseManager
from db_backend import create_backend
from footnote_scan_core import verify_candidates
from footnotes import content_to_list
from logger import Logger
from trad_simp import convert_text

FIELDS = ("term_zh", "term_en", "body", "sentence", "chapter_title")
COLS = ("id", "chapter_number", "status") + FIELDS


def verified_count(rows, db, book_id):
    """How many of these candidate rows can be anchored in their chapter source?"""
    by_ch = {}
    for r in rows:
        by_ch.setdefault(r["chapter_number"], []).append(r)
    ok = bad = skipped = 0
    for cn, group in sorted(by_ch.items()):
        ch = db.get_chapter(book_id=book_id, chapter_number=cn)
        source = "\n".join(content_to_list(ch.get("untranslated"))) if ch else ""
        if not source.strip():
            skipped += len(group)
            continue
        kept, dropped = verify_candidates(group, source)
        ok += len(kept)
        bad += len(dropped)
    return ok, bad, skipped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--book-id", type=int, required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    config = TranslationConfig()
    db = DatabaseManager(config, Logger(config))

    backend = create_backend()
    conn = backend.get_connection()
    cur = conn.cursor()
    cur.execute(
        f"SELECT {', '.join(COLS)} FROM footnote_candidates WHERE book_id=? "
        "ORDER BY chapter_number, id",
        (args.book_id,))
    rows = [dict(zip(COLS, r)) for r in cur.fetchall()]

    if not rows:
        print(f"Book {args.book_id}: no footnote candidates stored.")
        conn.close()
        return

    before_ok, before_bad, skipped = verified_count(rows, db, args.book_id)

    changed = []
    for r in rows:
        new = {f: convert_text(r[f]) if r[f] else r[f] for f in FIELDS}
        if any(new[f] != r[f] for f in FIELDS):
            changed.append((r, new))

    after_rows = []
    for r in rows:
        merged = dict(r)
        for f in FIELDS:
            merged[f] = convert_text(r[f]) if r[f] else r[f]
        after_rows.append(merged)
    after_ok, after_bad, _ = verified_count(after_rows, db, args.book_id)

    print(f"Book {args.book_id}: {len(rows)} footnote candidate(s)")
    print(f"  rows needing conversion : {len(changed)}")
    print(f"  verifies against source : {before_ok} ok / {before_bad} unverified"
          f"{f' / {skipped} unscannable' if skipped else ''}   (BEFORE)")
    print(f"                          : {after_ok} ok / {after_bad} unverified"
          f"{f' / {skipped} unscannable' if skipped else ''}   (AFTER)")
    print(f"  rescued by conversion   : {after_ok - before_ok}")

    if changed:
        print("\n--- conversions ---")
        for r, new in changed:
            bits = [f"{f}: {r[f]!r} -> {new[f]!r}"
                    for f in FIELDS if new[f] != r[f] and f in ("term_zh", "term_en")]
            other = [f for f in FIELDS if new[f] != r[f] and f not in ("term_zh", "term_en")]
            tail = f"  (+{', '.join(other)})" if other else ""
            print(f"  #{r['id']} ch{r['chapter_number']} [{r['status']}] "
                  f"{'; '.join(bits) if bits else '(no term change)'}{tail}")

    if not args.apply:
        print("\n[DRY RUN] re-run with --apply to write.")
        conn.close()
        return

    for r, new in changed:
        cur.execute(
            f"UPDATE footnote_candidates SET {', '.join(f + ' = ?' for f in FIELDS)} "
            "WHERE id = ?",
            tuple(new[f] for f in FIELDS) + (r["id"],))
    conn.commit()
    conn.close()
    print(f"\nAPPLIED: {len(changed)} candidate row(s) converted.")


if __name__ == "__main__":
    main()
