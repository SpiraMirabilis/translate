"""Per-book entity snapshots for concurrent translation jobs.

`DatabaseManager.entities` is a single cache holding one book at a time. When
two books translate concurrently, both jobs would reload it and swap each
other's glossary out mid-chapter — book A's prose prompted with book B's
entities. That corruption is semantic, not structural, so `_entities_lock`
cannot prevent it.

`get_entities_snapshot()` gives each run its own dict and never touches the
shared cache, which stays for the admin Entities/Dictionary pages.
"""


def _seed(db):
    a = db.create_book("Book A", author="A")
    b = db.create_book("Book B", author="B")
    db.add_entity("characters", "张三", "Zhang San", book_id=a)
    db.add_entity("characters", "李四", "Li Si", book_id=b)
    db.add_entity("places", "昆仑", "Kunlun")  # global (book_id=None)
    return a, b


def test_snapshot_is_scoped_to_its_book(db):
    a, b = _seed(db)

    snap_a = db.get_entities_snapshot(a)
    snap_b = db.get_entities_snapshot(b)

    assert snap_a["characters"]["张三"]["translation"] == "Zhang San"
    assert "李四" not in snap_a["characters"]

    assert snap_b["characters"]["李四"]["translation"] == "Li Si"
    assert "张三" not in snap_b["characters"]

    # Global entities reach both books.
    assert snap_a["places"]["昆仑"]["translation"] == "Kunlun"
    assert snap_b["places"]["昆仑"]["translation"] == "Kunlun"


def test_snapshot_does_not_touch_the_shared_cache(db):
    a, b = _seed(db)

    db._load_entities(book_id=a)
    cached = db.entities
    assert "张三" in cached["characters"]

    # The other book's snapshot must not steal the cache out from under book A.
    db.get_entities_snapshot(b)

    assert db.entities is cached
    assert "张三" in db.entities["characters"]
    assert "李四" not in db.entities["characters"]


def test_snapshots_are_independent_objects(db):
    a, _ = _seed(db)

    first = db.get_entities_snapshot(a)
    second = db.get_entities_snapshot(a)
    assert first is not second
    assert first["characters"] is not second["characters"]

    # A run mutating its own glossary cannot reach another run's, nor the cache.
    db._load_entities(book_id=a)
    first["characters"]["新人"] = {"translation": "Newcomer"}
    assert "新人" not in second["characters"]
    assert "新人" not in db.entities["characters"]


def test_load_entities_still_populates_the_cache(db):
    a, b = _seed(db)

    assert db._load_entities(book_id=a) == db.entities
    assert "张三" in db.entities["characters"]

    db._load_entities(book_id=b)
    assert "李四" in db.entities["characters"]
    assert "张三" not in db.entities["characters"]
