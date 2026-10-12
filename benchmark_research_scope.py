"""Benchmark scoped entity research against a disposable, synthetic SQLite DB.

Run: python benchmark_research_scope.py --chapters 900 --entities 5000 --runs 3
No application configuration, secrets, or translation database is loaded.
"""
import argparse
import json
import random
import sqlite3
import statistics
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from mcp_server.research_scope import ResearchScope, scope, verified_entities


class SyntheticDB:
    def __init__(self, path):
        self.path = path

    @contextmanager
    def _conn(self, *, dict_rows=False):
        conn = sqlite3.connect(self.path)
        if dict_rows:
            conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()


def populate(db, chapters, entities):
    rng = random.Random(19)
    mentions = [[] for _ in range(chapters)]
    records = []
    revisions = []
    for index in range(entities):
        key = "张陈李王赵林周苏萧叶陆顾韩沈方秦云白楚段山夜风天"[index % 24] + chr(0x4E00 + index // 100) + chr(0x4E00 + index % 100)
        rendering = f"{('Elder', 'City', 'Sect', 'Technique')[index % 4]} {index:05d}"
        origin = rng.randrange(chapters)
        for number in {origin, *[rng.randrange(origin, chapters) for _ in range(3)]}:
            mentions[number].append((key, rendering))
        # A later, unwitnessed correction must remain hidden at this boundary.
        current = rendering if index % 5 else f"UNWITNESSED {index:05d}"
        note = f"Observed synthetic note {index:05d}"
        revisions.append((len(revisions) + 1, 1, index + 1, origin + 1, None, note))
        if index % 7 == 0:
            future_note = f"FUTURE_NOTE {index:05d}"
            revisions.append((len(revisions) + 1, 1, index + 1, chapters + 1, note, future_note))
            note = future_note
        records.append((index + 1, 1, key, current, origin + 1, note))

    source = "夜色渐深，山间的风吹过树林。他沿着小路继续前行，心中思索着先前发生的事情。" * 160
    translated = ("The night grew dark as wind swept the forest. He followed the path, "
                  "thinking of the events of the day. ") * 200
    with db._conn() as conn:
        conn.executescript("""
            CREATE TABLE entities (id INTEGER, book_id INTEGER, untranslated TEXT,
                translation TEXT, origin_chapter INTEGER, note TEXT);
            CREATE TABLE chapters (book_id INTEGER, chapter_number INTEGER,
                untranslated_content TEXT, translated_content TEXT);
            CREATE TABLE entity_note_revisions (id INTEGER, book_id INTEGER,
                entity_id INTEGER, chapter_number INTEGER, previous_note TEXT, new_note TEXT);
        """)
        conn.executemany("INSERT INTO entities VALUES (?, ?, ?, ?, ?, ?)", records)
        conn.executemany("INSERT INTO entity_note_revisions VALUES (?, ?, ?, ?, ?, ?)", revisions)
        rows = [(1, number + 1,
                 json.dumps([source, "，".join(key for key, _ in terms)], ensure_ascii=False),
                 json.dumps([translated, ", ".join(value for _, value in terms)]))
                for number, terms in enumerate(mentions)]
        conn.executemany("INSERT INTO chapters VALUES (?, ?, ?, ?)", rows)
        conn.commit()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chapters", type=int, default=900)
    parser.add_argument("--entities", type=int, default=5000)
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    if min(args.chapters, args.entities, args.runs) < 1:
        parser.error("all counts must be positive")
    if args.entities < 2:
        parser.error("--entities must be at least 2 for a witnessed exact-match search")
    with tempfile.TemporaryDirectory(prefix="t9-research-benchmark-") as root:
        db = SyntheticDB(str(Path(root) / "synthetic.db"))
        populate(db, args.chapters, args.entities)
        token = scope.set(ResearchScope(1, args.chapters))
        try:
            timings = []
            search_timings = []
            with db._conn() as conn:
                query = conn.execute("SELECT untranslated FROM entities WHERE translation NOT LIKE 'UNWITNESSED%' "
                                     "ORDER BY origin_chapter DESC LIMIT 1").fetchone()[0]
            for _ in range(args.runs):
                started = time.perf_counter()
                results = verified_entities(db)
                timings.append(time.perf_counter() - started)
                assert len(results) == args.entities - (args.entities + 4) // 5
                assert all("UNWITNESSED" not in item["translation"] for item in results)
                assert all("FUTURE_NOTE" not in item.get("note", "") for item in results)
                started = time.perf_counter()
                found = verified_entities(db, matches=lambda ent: ent["untranslated"] == query)
                search_timings.append(time.perf_counter() - started)
                assert len(found) == 1 and found[0]["untranslated"] == query
            print(json.dumps({"chapters": args.chapters, "entities": args.entities,
                              "database_mib": round(Path(db.path).stat().st_size / 1024**2, 2),
                              "verified": len(results), "seconds": timings,
                              "median_seconds": statistics.median(timings),
                              "exact_search_seconds": search_timings,
                              "exact_search_median_seconds": statistics.median(search_timings)}, indent=2))
        finally:
            scope.reset(token)


if __name__ == "__main__":
    main()
