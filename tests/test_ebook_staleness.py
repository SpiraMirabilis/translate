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

    def test_orphan_stamp_counts_as_missing_locally(self, tmp_path):
        """Spaces off: a stamp without its file is no copy at all."""
        path = str(tmp_path / "1.azw3")
        ebook_build.write_stamp(path, "x")
        assert ebook_build.age_minutes(path) == float("inf")
        assert not ebook_build.is_current(path, "x")

    def test_offloaded_stamp_keeps_version_and_age(self, tmp_path):
        """Spaces on: the stamp outlives the offloaded file and still says which
        CDN version is served and how old it is."""
        path = str(tmp_path / "1.azw3")
        ebook_build.write_stamp(path, "x")
        assert ebook_build.served_version(path) == "x"
        assert ebook_build.age_minutes(path, require_file=False) < 1


class FakeSpaces:
    """In-memory bucket standing in for spaces.py's network calls."""

    def __init__(self):
        self.objects = {}
        self.fail_upload = False
        self.short_upload = False

    def install(self, monkeypatch):
        import spaces
        monkeypatch.setattr(spaces, "is_enabled", lambda config=None: True)
        monkeypatch.setattr(spaces, "key_for", lambda config, rel: rel)
        monkeypatch.setattr(spaces, "public_url", lambda config, key: f"https://cdn.test/{key}")
        monkeypatch.setattr(spaces, "upload", self.upload)
        monkeypatch.setattr(spaces, "exists", lambda config, key: key in self.objects)
        monkeypatch.setattr(spaces, "object_size",
                            lambda config, key: (len(self.objects[key])
                                                 if key in self.objects else None))
        monkeypatch.setattr(spaces, "prune_epub_versions", self._pruner("epub"))
        monkeypatch.setattr(spaces, "prune_azw3_versions", self._pruner("azw3"))
        return self

    def upload(self, config, local_path, key, content_type=None):
        if self.fail_upload:
            return False
        with open(local_path, "rb") as fh:
            data = fh.read()
        self.objects[key] = data[:-1] if self.short_upload else data
        return True

    def _pruner(self, fmt):
        def prune(config, book_id, keep_key):
            prefix = f"{fmt}/{book_id}/"
            for k in [k for k in self.objects if k.startswith(prefix) and k != keep_key]:
                del self.objects[k]
        return prune


@pytest.fixture
def fake_spaces(monkeypatch):
    return FakeSpaces().install(monkeypatch)


class TestOffload:
    """Spaces on: the local file is a build product, deleted once its upload is
    confirmed; the stamp stays and records what the CDN serves."""

    def test_offload_confirms_then_drops(self, db, book, fake_spaces):
        basis, _ = db.ebook_version_basis(book)
        path = _plant(db, book, "epub", basis, body=b"fresh")
        key = ebook_build.offload(db.config, book, "epub", path, basis)
        assert key and fake_spaces.objects[key] == b"fresh"
        assert not os.path.exists(path)
        assert ebook_build.read_stamp(path) == str(basis)

    @pytest.mark.parametrize("failure", ["fail_upload", "short_upload"])
    def test_unconfirmed_upload_keeps_the_local_copy(self, db, book, fake_spaces, failure):
        setattr(fake_spaces, failure, True)
        basis, _ = db.ebook_version_basis(book)
        path = _plant(db, book, "epub", basis, body=b"fresh")
        assert ebook_build.offload(db.config, book, "epub", path, basis) is None
        assert os.path.exists(path)

    def test_offload_prunes_other_versions(self, db, book, fake_spaces):
        old, _ = db.ebook_version_basis(book)
        ebook_build.offload(db.config, book, "epub", _plant(db, book, "epub", old), old)
        _save(db, book, 3)
        new, _ = db.ebook_version_basis(book)
        key = ebook_build.offload(db.config, book, "epub", _plant(db, book, "epub", new), new)
        assert list(fake_spaces.objects) == [key]

    def test_on_demand_epub_redirects_and_leaves_no_file(self, public_client, db, book,
                                                         fake_spaces):
        r = public_client.get(f"/api/public/books/{book}/epub", follow_redirects=False)
        assert r.status_code == 302
        path = os.path.join(db._epub_cache_dir(), f"{book}.epub")
        assert not os.path.exists(path)
        basis, _ = db.ebook_version_basis(book)
        assert ebook_build.read_stamp(path) == str(basis)
        key = ebook_build.spaces_key(db.config, book, "epub", basis)
        assert r.headers["location"] == f"https://cdn.test/{key}"

    def test_on_demand_azw3_offloads_both_and_leaves_no_files(self, public_client, db,
                                                              book, fake_spaces,
                                                              monkeypatch):
        import azw3
        monkeypatch.setattr(azw3, "is_available", lambda: True)
        seen = {}

        def convert(epub_path, azw3_path, logger):
            seen["epub_on_disk"] = os.path.exists(epub_path)
            with open(azw3_path, "wb") as fh:
                fh.write(b"kindle")
            return True
        monkeypatch.setattr(azw3, "convert_epub_to_azw3", convert)

        r = public_client.get(f"/api/public/books/{book}/azw3", follow_redirects=False)
        assert r.status_code == 302
        assert seen["epub_on_disk"]  # the source stayed until conversion was done
        basis, _ = db.ebook_version_basis(book)
        for fmt in ("epub", "azw3"):
            assert ebook_build.spaces_key(db.config, book, fmt, basis) in fake_spaces.objects
            assert not os.path.exists(os.path.join(db._epub_cache_dir(), f"{book}.{fmt}"))

    def test_stale_offloaded_copy_is_found_by_its_stamp(self, public_client, db, book,
                                                        fake_spaces, monkeypatch):
        old, _ = db.ebook_version_basis(book)
        old_key = ebook_build.offload(db.config, book, "epub",
                                      _plant(db, book, "epub", old), old)
        _save(db, book, 3)  # stale now; only the stamp is on disk

        from output_formatter import OutputFormatter

        def no_build(*a, **kw):
            raise AssertionError("must not rebuild while a stale copy exists")
        monkeypatch.setattr(OutputFormatter, "save_book_as_epub", no_build)

        r = public_client.get(f"/api/public/books/{book}/epub", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == f"https://cdn.test/{old_key}"

    def test_build_race_loser_redirects_instead_of_rebuilding(self, db, book, fake_spaces,
                                                               monkeypatch):
        """A request that waited on the lock finds the winner's upload."""
        from web.api import public as public_api
        basis, _ = db.ebook_version_basis(book)
        key = ebook_build.offload(db.config, book, "epub",
                                  _plant(db, book, "epub", basis), basis)
        monkeypatch.setattr(public_api, "_db", db)
        monkeypatch.setattr(public_api, "_build_public_epub_locked",
                            lambda *a: (_ for _ in ()).throw(AssertionError("rebuilt")))
        assert public_api._build_and_offload(book, {}, "epub", basis) == ("cdn", key)


class TestPrewarmOffload:
    @staticmethod
    def _args(**kw):
        base = dict(force=True, quiet_minutes=15, max_stale_minutes=30, dry_run=False)
        base.update(kw)
        return argparse.Namespace(**base)

    def test_prewarm_uploads_and_leaves_only_stamps(self, db, book, fake_spaces,
                                                     monkeypatch):
        import azw3
        import prewarm_ebooks

        def convert(epub_path, azw3_path, logger):
            assert os.path.exists(epub_path)
            with open(azw3_path, "wb") as fh:
                fh.write(b"kindle")
            return True
        monkeypatch.setattr(azw3, "convert_epub_to_azw3", convert)

        b = db.get_book(book)
        status = prewarm_ebooks._process_book(db, db.config, db.logger, b, self._args(),
                                              azw3_ok=True, spaces_on=True)
        assert status.startswith("built: epub, azw3"), status
        basis, _ = db.ebook_version_basis(book)
        for fmt in ("epub", "azw3"):
            path = os.path.join(db._epub_cache_dir(), f"{book}.{fmt}")
            assert not os.path.exists(path)
            assert ebook_build.read_stamp(path) == str(basis)
            assert ebook_build.spaces_key(db.config, book, fmt, basis) in fake_spaces.objects

        again = prewarm_ebooks._process_book(db, db.config, db.logger, b, self._args(),
                                             azw3_ok=True, spaces_on=True)
        assert again == "skip: up-to-date"

    def test_failed_azw3_upload_keeps_it_local(self, db, book, fake_spaces, monkeypatch):
        import azw3
        import prewarm_ebooks

        def convert(epub_path, azw3_path, logger):
            with open(azw3_path, "wb") as fh:
                fh.write(b"kindle")
            fake_spaces.fail_upload = True  # EPUB went up; the AZW3 won't
            return True
        monkeypatch.setattr(azw3, "convert_epub_to_azw3", convert)

        status = prewarm_ebooks._process_book(db, db.config, db.logger, db.get_book(book),
                                              self._args(), azw3_ok=True, spaces_on=True)
        assert "AZW3 upload failed" in status
        cache = db._epub_cache_dir()
        assert os.path.exists(os.path.join(cache, f"{book}.azw3"))
        assert not os.path.exists(os.path.join(cache, f"{book}.epub"))  # CDN has it

    def test_sweep_drops_only_confirmed_copies(self, db, book, fake_spaces):
        import prewarm_ebooks
        basis, _ = db.ebook_version_basis(book)
        on_cdn = _plant(db, book, "epub", basis, body=b"same")
        fake_spaces.objects[ebook_build.spaces_key(db.config, book, "epub", basis)] = b"same"
        mismatched = _plant(db, book, "azw3", basis, body=b"local only")
        fake_spaces.objects[ebook_build.spaces_key(db.config, book, "azw3", basis)] = b"other"
        cache = db._epub_cache_dir()
        unstamped = os.path.join(cache, "999.epub")
        full = os.path.join(cache, f"{book}-full.epub")
        for p in (unstamped, full):
            with open(p, "wb") as fh:
                fh.write(b"x")

        _save(db, book, 3)  # the stamped versions above are stale from here on

        assert prewarm_ebooks.drop_offloaded_copies(db, db.config, dry_run=True) == (1, 4)
        assert os.path.exists(on_cdn)
        assert prewarm_ebooks.drop_offloaded_copies(db, db.config) == (1, 4)
        assert not os.path.exists(on_cdn)
        assert ebook_build.read_stamp(on_cdn) == str(basis)
        for p in (mismatched, unstamped, full):
            assert os.path.exists(p)

    def test_sweep_drops_anything_the_current_cdn_version_covers(self, db, book,
                                                                  fake_spaces):
        """A legacy unstamped AZW3 (or a mis-sized local rebuild) is dead weight
        once Spaces serves the current version; its stamp becomes that version."""
        import prewarm_ebooks
        cache = db._epub_cache_dir()
        os.makedirs(cache, exist_ok=True)
        legacy = os.path.join(cache, f"{book}.azw3")
        with open(legacy, "wb") as fh:
            fh.write(b"old kindle")
        current, _ = db.ebook_version_basis(book)
        fake_spaces.objects[ebook_build.spaces_key(db.config, book, "azw3", current)] = b"k"

        assert prewarm_ebooks.drop_offloaded_copies(db, db.config) == (1, 10)
        assert not os.path.exists(legacy)
        assert ebook_build.read_stamp(legacy) == str(current)
