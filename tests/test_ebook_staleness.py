"""Stale-while-rebuild ebook caching.

A content change marks a book's EPUB/AZW3 stale (it moves the version basis)
but keeps the files, so the public endpoints go on serving the old copy until
prewarm_ebooks.py rebuilds it — an AZW3 takes minutes, longer than Cloudflare
holds a request. Only changes that REMOVE content purge the files.
"""
import argparse
import os

import pytest

import ebook_build


@pytest.fixture(autouse=True)
def reset_public_limiter():
    from web.api import public as public_api

    public_api._public_limiter.reset()
    yield
    public_api._public_limiter.reset()


@pytest.fixture
def public_client(web_app):
    from tests.api_client import SyncASGIClient

    return SyncASGIClient(web_app, headers={"Origin": "http://testserver"})


def _save(db, book_id, n, text="line"):
    db.save_chapter(
        book_id=book_id, chapter_number=n,
        untranslated_content=[f"第{n}章", "原文"],
        translated_content=[f"Chapter {n}", f"{text} {n}"],
        title=f"Title {n}",
    )


@pytest.fixture
def book(db):
    book_id = db.create_book("Stale Book", author="A")
    for n in (1, 2):
        _save(db, book_id, n)
    return book_id


def _plant(db, book_id, ext, basis, body=b"old-bytes"):
    """Put a built artifact on disk, stamped with `basis`."""
    path = os.path.join(db._epub_cache_dir(), f"{book_id}.{ext}")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(body)
    ebook_build.write_stamp(path, basis)
    return path


class TestInvalidation:
    def test_soft_invalidate_keeps_files_and_moves_the_basis(self, db, book):
        before, _ = db.ebook_version_basis(book)
        path = _plant(db, book, "epub", before)

        db.invalidate_epub_cache(book)

        after, _ = db.ebook_version_basis(book)
        assert after != before
        assert os.path.exists(path)
        assert not ebook_build.is_current(path, after)  # stale, not gone

    def test_edit_without_modified_date_bump_still_goes_stale(self, db, book):
        """replace_in_chapters never touches modified_date; it relied on the
        old delete, so the invalidation stamp has to carry it now."""
        before, _ = db.ebook_version_basis(book)
        db.replace_in_chapters(book, "line", "verse")
        after, _ = db.ebook_version_basis(book)
        assert after != before

    def test_purge_deletes_files(self, db, book):
        basis, _ = db.ebook_version_basis(book)
        paths = [_plant(db, book, ext, basis) for ext in ("epub", "azw3")]
        db.invalidate_epub_cache(book, purge=True)
        for p in paths:
            assert not os.path.exists(p)
            assert not os.path.exists(ebook_build.stamp_path(p))

    def test_new_chapter_is_soft(self, db, book):
        basis, _ = db.ebook_version_basis(book)
        path = _plant(db, book, "epub", basis)
        _save(db, book, 3)
        assert os.path.exists(path)

    def test_deleting_a_chapter_purges(self, db, book):
        basis, _ = db.ebook_version_basis(book)
        path = _plant(db, book, "epub", basis)
        db.delete_chapter(book_id=book, chapter_number=2)
        assert not os.path.exists(path)

    def test_unpublishing_a_live_chapter_purges(self, db, book):
        basis, _ = db.ebook_version_basis(book)
        path = _plant(db, book, "epub", basis)
        db.set_chapter_published(book, 2, None)
        assert not os.path.exists(path)

    def test_rescheduling_a_live_chapter_into_the_future_purges(self, db, book):
        basis, _ = db.ebook_version_basis(book)
        path = _plant(db, book, "epub", basis)
        db.set_chapters_published(book, [(1, "2999-01-01T00:00:00")])
        assert not os.path.exists(path)

    def test_publishing_and_redrafting_a_draft_are_soft(self, db, book):
        db.set_chapter_published(book, 2, None)
        basis, _ = db.ebook_version_basis(book)
        path = _plant(db, book, "epub", basis)

        db.set_chapter_published(book, 2, "2000-01-01T00:00:00")  # publish it
        assert os.path.exists(path)
        db.set_chapters_published(book, [(2, "2000-01-01T00:00:00")])  # already live
        assert os.path.exists(path)


class TestServing:
    def test_epub_built_on_demand_only_when_missing(self, public_client, db, book):
        r = public_client.get(f"/api/public/books/{book}/epub")
        assert r.status_code == 200
        path = os.path.join(db._epub_cache_dir(), f"{book}.epub")
        basis, _ = db.ebook_version_basis(book)
        assert ebook_build.is_current(path, basis)

    def test_stale_epub_is_served_without_rebuilding(self, public_client, db, book,
                                                      monkeypatch):
        old_basis, _ = db.ebook_version_basis(book)
        _plant(db, book, "epub", old_basis, body=b"the old epub")
        _save(db, book, 3)  # book moves on; the copy is now stale

        from output_formatter import OutputFormatter

        def no_build(*a, **kw):
            raise AssertionError("must not rebuild while a stale copy exists")
        monkeypatch.setattr(OutputFormatter, "save_book_as_epub", no_build)

        r = public_client.get(f"/api/public/books/{book}/epub")
        assert r.status_code == 200
        assert r.content == b"the old epub"

    def test_stale_azw3_is_served_and_reported_cached(self, public_client, db, book,
                                                       monkeypatch):
        import azw3
        monkeypatch.setattr(azw3, "is_available", lambda: True)

        def no_convert(*a, **kw):
            raise AssertionError("must not convert while a stale copy exists")
        monkeypatch.setattr(azw3, "convert_epub_to_azw3", no_convert)

        status = public_client.get(f"/api/public/books/{book}/azw3/status").json()
        assert status == {"available": True, "cached": False, "stale": False}

        old_basis, _ = db.ebook_version_basis(book)
        _plant(db, book, "azw3", old_basis, body=b"the old azw3")
        status = public_client.get(f"/api/public/books/{book}/azw3/status").json()
        assert status == {"available": True, "cached": True, "stale": False}

        db.invalidate_epub_cache(book)
        status = public_client.get(f"/api/public/books/{book}/azw3/status").json()
        assert status == {"available": True, "cached": True, "stale": True}

        r = public_client.get(f"/api/public/books/{book}/azw3")
        assert r.status_code == 200
        assert r.content == b"the old azw3"


class TestPrewarmGuard:
    @staticmethod
    def _args(**kw):
        base = dict(force=False, quiet_minutes=15, max_stale_minutes=30)
        base.update(kw)
        return argparse.Namespace(**base)

    def test_quiet_book_builds(self):
        from prewarm_ebooks import _defer_build
        assert not _defer_build(20, 5, self._args())

    def test_busy_book_with_a_young_copy_waits(self):
        from prewarm_ebooks import _defer_build
        assert _defer_build(2, 10, self._args())

    def test_busy_book_with_an_old_copy_rebuilds(self):
        from prewarm_ebooks import _defer_build
        assert not _defer_build(2, 31, self._args())

    def test_busy_book_with_no_copy_builds(self, tmp_path):
        from prewarm_ebooks import _defer_build
        missing = ebook_build.age_minutes(str(tmp_path / "nope.epub"))
        assert missing == float("inf")
        assert not _defer_build(2, missing, self._args())

    def test_force_overrides(self):
        from prewarm_ebooks import _defer_build
        assert not _defer_build(2, 1, self._args(force=True))

    def test_orphan_stamp_counts_as_missing(self, tmp_path):
        path = str(tmp_path / "1.azw3")
        ebook_build.write_stamp(path, "x")
        assert ebook_build.age_minutes(path) == float("inf")
        assert ebook_build.served_version(path) is None
