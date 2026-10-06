"""Reader-submitted error reports: repo round-trip and the public POST guards.

Turnstile is stubbed out module-wide — a developer with a real
CF_TURNSTILE_SECRET_KEY in .env would otherwise make a live siteverify call per
test. What these exercise is the book/chapter visibility gates, the type enum,
the kill switch and the rate limiter.
"""
import pytest

from web.api import error_reports_public


# ------------------------------------------------------------------
# Repo
# ------------------------------------------------------------------

def test_repo_round_trip(db):
    book_id = db.create_book("Reported Book", author="Author")

    rid = db.create_error_report({
        "book_id": book_id,
        "chapter_number": 7,
        "report_type": "wrong_term",
        "quote": "Chen Yuan",
        "problem": "Name is rendered two ways.",
        "suggested_fix": "Use Chen Yan throughout.",
        "reporter_email": "reader@example.com",
        "ip": "203.0.113.9",
        "user_agent": "pytest",
    })
    assert rid

    row = db.get_error_report(rid)
    assert row["book_id"] == book_id
    assert row["chapter_number"] == 7
    assert row["status"] == "new"
    assert row["book_title"] == "Reported Book"
    assert row["quote"] == "Chen Yuan"

    assert db.count_error_reports() == 1
    assert db.count_error_reports(status="new") == 1
    assert db.count_error_reports(status="resolved") == 0

    db.update_error_report(rid, {"status": "resolved", "admin_notes": "fixed in ch7"})
    row = db.get_error_report(rid)
    assert row["status"] == "resolved"
    assert row["admin_notes"] == "fixed in ch7"

    assert [r["id"] for r in db.list_error_reports(status="resolved")] == [rid]
    assert db.list_error_reports(status="new") == []
    assert [r["id"] for r in db.list_error_reports(book_id=book_id)] == [rid]

    db.delete_error_report(rid)
    assert db.get_error_report(rid) is None
    assert db.count_error_reports() == 0


def test_book_wide_report_has_null_chapter(db):
    book_id = db.create_book("Book Wide", author="A")
    rid = db.create_error_report({
        "book_id": book_id,
        "chapter_number": None,
        "report_type": "other",
        "problem": "Chapter numbering is off after ch40.",
    })
    assert db.get_error_report(rid)["chapter_number"] is None


def test_update_ignores_unwhitelisted_fields(db):
    book_id = db.create_book("Whitelist", author="A")
    rid = db.create_error_report({
        "book_id": book_id, "report_type": "typo", "problem": "x",
    })
    db.update_error_report(rid, {"problem": "rewritten", "book_id": 999})
    row = db.get_error_report(rid)
    assert row["problem"] == "x"
    assert row["book_id"] == book_id


# ------------------------------------------------------------------
# Public POST
# ------------------------------------------------------------------

@pytest.fixture
def pub_client(public_web_app):
    from tests.api_client import SyncASGIClient

    return SyncASGIClient(public_web_app, headers={"Origin": "http://testserver"})


@pytest.fixture(autouse=True)
def stub_turnstile(monkeypatch):
    """No live siteverify calls, and no dependence on whether the dev running
    the suite happens to have a CF secret configured."""
    async def ok(token, ip):
        return (True, None)

    monkeypatch.setattr("web.services.turnstile.verify", ok)


@pytest.fixture(autouse=True)
def reset_limiters():
    """The limiters are module-level and shared across tests in a process."""
    error_reports_public._limiter_short.reset()
    error_reports_public._limiter_long.reset()
    yield
    error_reports_public._limiter_short.reset()
    error_reports_public._limiter_long.reset()


@pytest.fixture
def reported_book(db):
    book_id = db.create_book("Public Book", author="Author")
    db.save_chapter(
        book_id=book_id,
        chapter_number=1,
        untranslated_content=["第1章", "原文"],
        translated_content=["Chapter 1", "A public line."],
        title="Title 1",
    )
    return book_id


def _payload(book_id, **over):
    body = {
        "book_id": book_id,
        "chapter_number": 1,
        "report_type": "mistranslation",
        "quote": "A public line.",
        "problem": "This sentence reverses the meaning of the source.",
        "suggested_fix": "",
        "reporter_email": "",
        "turnstile_token": "",
    }
    body.update(over)
    return body


def test_submit_creates_row(pub_client, reported_book, db):
    resp = pub_client.post("/api/public/error-reports", json=_payload(reported_book))
    assert resp.status_code == 200, resp.text
    rid = resp.json()["id"]

    row = db.get_error_report(rid)
    assert row["book_id"] == reported_book
    assert row["chapter_number"] == 1
    assert row["report_type"] == "mistranslation"
    assert row["status"] == "new"
    # Blank optionals are stored as NULL, not empty strings.
    assert row["suggested_fix"] is None
    assert row["reporter_email"] is None
    assert row["user_agent"] is not None


def test_book_wide_submit(pub_client, reported_book, db):
    resp = pub_client.post("/api/public/error-reports",
                           json=_payload(reported_book, chapter_number=None, quote=None))
    assert resp.status_code == 200, resp.text
    assert db.get_error_report(resp.json()["id"])["chapter_number"] is None


def test_unknown_report_type_rejected(pub_client, reported_book):
    resp = pub_client.post("/api/public/error-reports",
                           json=_payload(reported_book, report_type="spam"))
    assert resp.status_code == 400


def test_empty_problem_rejected(pub_client, reported_book):
    resp = pub_client.post("/api/public/error-reports",
                           json=_payload(reported_book, problem="   "))
    assert resp.status_code == 400


def test_bad_email_rejected(pub_client, reported_book):
    resp = pub_client.post("/api/public/error-reports",
                           json=_payload(reported_book, reporter_email="not-an-email"))
    assert resp.status_code == 400


def test_unknown_book_404(pub_client):
    resp = pub_client.post("/api/public/error-reports", json=_payload(999999))
    assert resp.status_code == 404


def test_private_book_404(pub_client, reported_book, db):
    db.update_book(reported_book, is_public=False)
    resp = pub_client.post("/api/public/error-reports", json=_payload(reported_book))
    assert resp.status_code == 404


def test_unpublished_chapter_404(pub_client, reported_book, db):
    """A draft chapter isn't publicly readable, so it can't be reported on —
    otherwise the endpoint is an oracle for unreleased chapter numbers."""
    db.set_chapter_published(reported_book, 1, None)
    resp = pub_client.post("/api/public/error-reports", json=_payload(reported_book))
    assert resp.status_code == 404


def test_missing_chapter_404(pub_client, reported_book):
    resp = pub_client.post("/api/public/error-reports",
                           json=_payload(reported_book, chapter_number=4242))
    assert resp.status_code == 404


def test_rate_limited(pub_client, reported_book):
    for _ in range(3):
        assert pub_client.post("/api/public/error-reports",
                               json=_payload(reported_book)).status_code == 200
    resp = pub_client.post("/api/public/error-reports", json=_payload(reported_book))
    assert resp.status_code == 429


def test_kill_switch(pub_client, reported_book, monkeypatch):
    monkeypatch.setattr(error_reports_public, "reports_enabled", lambda: False)
    resp = pub_client.post("/api/public/error-reports", json=_payload(reported_book))
    assert resp.status_code == 403
    assert pub_client.get("/api/public/error-reports/enabled").json()["enabled"] is False


def test_enabled_endpoint_default(pub_client):
    assert pub_client.get("/api/public/error-reports/enabled").json()["enabled"] is True


def test_quote_truncated_to_cap(pub_client, reported_book, db):
    """The Pydantic max_length rejects anything longer, so an over-long quote is
    a 422 rather than a silent truncation — the modal caps it client-side too."""
    resp = pub_client.post("/api/public/error-reports",
                           json=_payload(reported_book, quote="x" * 501))
    assert resp.status_code == 422


# ------------------------------------------------------------------
# Admin surface
# ------------------------------------------------------------------

def test_admin_list_and_triage(admin_client, db):
    book_id = db.create_book("Admin Triage", author="A")
    rid = db.create_error_report({
        "book_id": book_id, "chapter_number": 3,
        "report_type": "typo", "problem": "teh",
    })

    listing = admin_client.get("/api/error-reports")
    assert listing.status_code == 200, listing.text
    assert [i["id"] for i in listing.json()["items"]] == [rid]

    assert admin_client.get("/api/error-reports/count?status=new").json()["count"] == 1

    upd = admin_client.put(f"/api/error-reports/{rid}", json={"status": "resolved"})
    assert upd.status_code == 200
    row = admin_client.get(f"/api/error-reports/{rid}").json()
    assert row["status"] == "resolved"
    assert row["reviewed_at"]

    assert admin_client.put(f"/api/error-reports/{rid}",
                            json={"status": "nonsense"}).status_code == 400

    assert admin_client.delete(f"/api/error-reports/{rid}").status_code == 200
    assert admin_client.get(f"/api/error-reports/{rid}").status_code == 404


def test_admin_routes_require_auth(api_client, db):
    assert api_client.get("/api/error-reports").status_code == 401


def test_admin_routes_absent_from_public_process(public_web_app):
    paths = {getattr(r, "path", "") for r in public_web_app.routes}
    assert not any(p.startswith("/api/error-reports") for p in paths)
    assert "/api/public/error-reports" in paths


def test_kill_switch_round_trips_through_the_settings_api(admin_client, tmp_path, monkeypatch):
    """The Settings checkbox has to persist: GET must report the switch and PUT
    must accept it, or the box always reads checked and an untick is dropped."""
    import settings_store
    # web_app doesn't redirect the store; never write the real settings.json.
    monkeypatch.setattr(settings_store, "_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setattr(settings_store, "_data", None)
    monkeypatch.setattr(settings_store, "_mtime_ns", None)
    monkeypatch.setenv("ERROR_REPORTS_ENABLED", "1")  # restored after update() syncs env

    assert admin_client.get("/api/settings").json()["error_reports_enabled"] is True

    resp = admin_client.put("/api/settings", json={"error_reports_enabled": False})
    assert resp.status_code == 200, resp.text
    assert settings_store.get("error_reports_enabled") is False
    assert admin_client.get("/api/settings").json()["error_reports_enabled"] is False
    assert admin_client.get("/api/public/error-reports/enabled").json()["enabled"] is False
