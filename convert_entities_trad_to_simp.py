#!/usr/bin/env python3
"""Convert a book's entity `untranslated` keys to simplified Chinese, merging
trad/simp duplicate pairs.

For each entity, the stored untranslated string is converted to simplified
(OpenCC t2s, via trad_simp.convert_text). Entities are then grouped by their
simplified form:

  * Singletons (only a traditional form) -> updated in place to simplified.
  * Duplicate groups where every translation matches (case/whitespace-insensitive)
    -> auto-merged: one survivor row keeps the simplified key + the best-cased
    translation + merged metadata (min origin_chapter, first non-null gender/note);
    the other rows are deleted.
  * Duplicate groups whose translations MATERIALLY differ -> left untouched and
    reported, for separate human resolution.

Dry-run by default; pass --apply to write.

Usage:
    python3 convert_entities_trad_to_simp.py --book-id 48
    python3 convert_entities_trad_to_simp.py --book-id 48 --apply
"""
import argparse
from collections import defaultdict

from db_backend import create_backend
from trad_simp import convert_text


def norm(t):
    return " ".join((t or "").lower().split())


def best_translation(translations):
    # Prefer the most capitalized (proper-noun style), then longest.
    return max(translations, key=lambda t: (sum(c.isupper() for c in t), len(t)))


def first(vals):
    for v in vals:
        if v not in (None, ""):
            return v
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--book-id", type=int, required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    backend = create_backend()
    conn = backend.get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, category, untranslated, translation, last_chapter, "
                "incorrect_translation, gender, origin_chapter, note "
                "FROM entities WHERE book_id=?", (args.book_id,))
    cols = ["id", "category", "untranslated", "translation", "last_chapter",
            "incorrect_translation", "gender", "origin_chapter", "note"]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]

    groups = defaultdict(list)
    for r in rows:
        groups[convert_text(r["untranslated"])].append(r)

    updates = []          # (id, simp_form) -- in-place key rewrite
    merges = []           # dict describing a trivial merge
    conflicts = []        # groups left for human resolution

    for simp, members in groups.items():
        forms = {m["untranslated"] for m in members}
        if len(members) == 1:
            m = members[0]
            if m["untranslated"] != simp:
                updates.append((m["id"], simp, m["untranslated"]))
            continue
        # duplicate group
        if len({norm(m["translation"]) for m in members}) > 1:
            conflicts.append((simp, members))
            continue
        # trivial merge -- prefer a member already in simplified form as the
        # survivor so its key needs no rewrite (avoids transient uq_entity clash).
        survivor = next((m for m in members if m["untranslated"] == simp), members[0])
        survivor_tr = best_translation([m["translation"] for m in members if m["translation"]] or [""])
        origins = [m["origin_chapter"] for m in members if m["origin_chapter"] is not None]
        merges.append({
            "simp": simp,
            "survivor_id": survivor["id"],
            "translation": survivor_tr,
            "gender": first([m["gender"] for m in members]),
            "note": first([m["note"] for m in members]),
            "origin_chapter": min(origins) if origins else None,
            "last_chapter": max([m["last_chapter"] for m in members if m["last_chapter"] is not None], default=None),
            "delete_ids": [m["id"] for m in members if m["id"] != survivor["id"]],
            "forms": forms,
            "members": members,
        })

    print(f"Book {args.book_id}: {len(rows)} entities")
    print(f"  in-place trad->simp updates : {len(updates)}")
    print(f"  trivial merges              : {len(merges)} (delete {sum(len(m['delete_ids']) for m in merges)} rows)")
    print(f"  MATERIAL conflicts (skipped): {len(conflicts)}")

    print("\n--- trivial merges ---")
    for m in merges:
        variants = " | ".join(sorted(f"{mm['untranslated']!r}->{mm['translation']!r}" for mm in m["members"]))
        print(f"  [{m['simp']}] keep id{m['survivor_id']} -> {m['translation']!r}  (orig {m['origin_chapter']})   {{{variants}}}")

    print("\n--- MATERIAL conflicts (left untouched) ---")
    for simp, members in conflicts:
        print(f"  [{simp}]")
        for mm in members:
            print(f"      id{mm['id']} ({mm['category']}, orig {mm['origin_chapter']}) {mm['untranslated']!r} -> {mm['translation']!r}")

    if not args.apply:
        print("\n[DRY RUN] re-run with --apply to write.")
        conn.close()
        return

    # Delete all merge duplicates first so survivor key rewrites can't transiently
    # collide with a sibling still holding the simplified key (uq_entity).
    for m in merges:
        for did in m["delete_ids"]:
            cur.execute("DELETE FROM entities WHERE id=?", (did,))
    for m in merges:
        cur.execute(
            "UPDATE entities SET untranslated=?, translation=?, gender=?, note=?, "
            "origin_chapter=?, last_chapter=? WHERE id=?",
            (m["simp"], m["translation"], m["gender"], m["note"],
             m["origin_chapter"], m["last_chapter"], m["survivor_id"]))
    for eid, simp, _ in updates:
        cur.execute("UPDATE entities SET untranslated=? WHERE id=?", (simp, eid))
    conn.commit()
    conn.close()
    print(f"\nAPPLIED: {len(updates)} updates, {len(merges)} merges "
          f"({sum(len(m['delete_ids']) for m in merges)} rows deleted). "
          f"{len(conflicts)} conflicts left for manual resolution.")


if __name__ == "__main__":
    main()
