"""Benchmark scoped entity research against a disposable, synthetic SQLite DB.

Run: python benchmark_research_scope.py --chapters 900 --entities 5000 --runs 3
No application configuration, secrets, or translation database is loaded.
"""
import argparse
import asyncio
import logging
import json
import random
import sqlite3
import statistics
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from db.entities_repo import EntitiesRepo
from get_entities import build_entities_payload
from mcp_server.research_scope import ResearchScope, scope
from mcp_server.server import build_server


class SyntheticDB(EntitiesRepo):
    def __init__(self, path):
        self.path = path
        self.logger = logging.getLogger(__name__)
        self.queries = []

    def get_book(self, book_id):
        return {"id": 1, "title": "Synthetic"} if book_id == 1 else None

    @contextmanager
    def _conn(self, *, dict_rows=False):
        conn = sqlite3.connect(self.path)
        conn.set_trace_callback(self.queries.append)
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
        # Current corrected renderings are retained even without prose evidence.
        current = rendering if index % 5 else f"UNWITNESSED {index:05d}"
        note = f"Observed synthetic note {index:05d}"
        revisions.append((len(revisions) + 1, 1, index + 1, origin + 1, None, note))
        if index % 7 == 0:
            future_note = f"FUTURE_NOTE {index:05d}"
            revisions.append((len(revisions) + 1, 1, index + 1, chapters + 1, note, future_note))
            note = future_note
        records.append((index + 1, 1, key, current, origin + 1, note,
                        "characters", "female", chapters, None))

    source = "夜色渐深，山间的风吹过树林。他沿着小路继续前行，心中思索着先前发生的事情。" * 160
    translated = ("The night grew dark as wind swept the forest. He followed the path, "
                  "thinking of the events of the day. ") * 200
    with db._conn() as conn:
        conn.executescript("""
            CREATE TABLE entities (id INTEGER, book_id INTEGER, untranslated TEXT,
                translation TEXT, origin_chapter INTEGER, note TEXT, category TEXT,
                gender TEXT, last_chapter INTEGER, incorrect_translation TEXT);
            CREATE TABLE chapters (book_id INTEGER, chapter_number INTEGER,
                untranslated_content TEXT, translated_content TEXT);
            CREATE TABLE entity_note_revisions (id INTEGER, book_id INTEGER,
                entity_id INTEGER, chapter_number INTEGER, previous_note TEXT, new_note TEXT);
        """)
        conn.executemany("INSERT INTO entities VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", records)
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
        parser.error("--entities must be at least 2 for an exact-match search")
    with tempfile.TemporaryDirectory(prefix="t9-research-benchmark-") as root:
        db = SyntheticDB(str(Path(root) / "synthetic.db"))
        populate(db, args.chapters, args.entities)
        server = build_server(db=db, read_only=True)
        token = scope.set(ResearchScope(1, args.chapters))
        try:
            timings, search_timings = [], []
            with db._conn() as conn:
                query = conn.execute("SELECT untranslated FROM entities ORDER BY origin_chapter DESC LIMIT 1").fetchone()[0]
            async def search():
                # Direct tool invocation keeps the synthetic DB independent of
                # application configuration; the ContextVar is still enforced.
                return await server._tool_manager.call_tool("t9_search_entities", {
                    "book_id": 1, "pattern": query, "field": "untranslated"})
            for _ in range(args.runs):
                db.queries.clear()
                started = time.perf_counter()
                payload = build_entities_payload(db, db.get_book(1),
                    as_of_chapter=args.chapters, origin_boundary=args.chapters)
                results = [entry for entries in payload["entities"].values() for entry in entries]
                timings.append(time.perf_counter() - started)
                assert len(results) == args.entities
                assert sum("UNWITNESSED" in item["translation"] for item in results) == (args.entities + 4) // 5
                assert all(item["gender"] == "female" and item.get("note") for item in results)
                assert all("FUTURE_NOTE" not in item["note"] for item in results)
                started = time.perf_counter()
                found = asyncio.run(search())
                search_timings.append(time.perf_counter() - started)
                assert query in found and "female" in found and "characters" in found
                # Chapter prose must never be transferred for glossary research.
                assert not any("FROM chapters" in sql for sql in db.queries)
            print(json.dumps({"chapters": args.chapters, "entities": args.entities,
                              "database_mib": round(Path(db.path).stat().st_size / 1024**2, 2),
                              "returned": len(results), "seconds": timings,
                              "median_seconds": statistics.median(timings),
                              "exact_search_seconds": search_timings,
                              "exact_search_median_seconds": statistics.median(search_timings),
                              "chapter_prose_queries": 0}, indent=2))
        finally:
            scope.reset(token)


if __name__ == "__main__":
    main()
