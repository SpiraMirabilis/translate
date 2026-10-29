"""Accepting a request as a specific book.

Our title often differs from the one the reader typed, so an accept can carry
the book the request became; the acceptance email then names and links it.
No mail is sent — email_sender.send_email is stubbed.
"""
import pytest
from fastapi import BackgroundTasks, HTTPException

from web.api import recommendations_admin
from web.api.recommendations_admin import RecommendationUpdate
from web.services import email_sender, recommendation_emails


@pytest.fixture
def sent(monkeypatch):
    calls = []

    def fake_send(to_addr, subject, text_body, html_body=None, extra_headers=None):
        calls.append({"to": to_addr, "subject": subject, "text": text_body, "html": html_body})
        return (True, "ok")

    monkeypatch.setattr(email_sender, "send_email", fake_send)
    monkeypatch.setenv("EMAIL_FROM", "editor@example.com")
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("SITE_BASE_URL", "https://reader.example.com/")
    return calls


@pytest.fixture
def api(db):
    recommendations_admin.init(db)
    return recommendations_admin


def _rec(db, title="Requested Title"):
    return db.create_recommendation({
        "novel_title": title,
        "source_url": "https://example.com/novel",
        "requester_name": "reader",
        "requester_email": "reader@example.org",
    })


def _accept(api, rec_id, **fields):
    tasks = BackgroundTasks()
    api.update_recommendation(rec_id, RecommendationUpdate(status="accepted", **fields), tasks)
    return tasks


def test_accept_with_book_links_it_and_names_it_in_the_email(db, api, sent):
    book_id = db.create_book("Our Better Title")
    rec_id = _rec(db)
    tasks = _accept(api, rec_id, book_id=book_id)

    rec = db.get_recommendation(rec_id)
    assert rec["status"] == "accepted"
    assert rec["book_id"] == book_id
    assert rec["book_title"] == "Our Better Title"

    for t in tasks.tasks:
        t.func(*t.args, **t.kwargs)
    [mail] = sent
    url = f"https://reader.example.com/library/book/{book_id}"
    assert "Requested Title" in mail["text"] and "Our Better Title" in mail["text"]
    assert url in mail["text"] and url in mail["html"]


def test_same_title_is_not_announced_as_a_rename(db, sent):
    book_id = db.create_book("Requested Title")
    rec_id = _rec(db)
    db.update_recommendation(rec_id, {"book_id": book_id})
    recommendation_emails.send_status_change(db, rec_id, "accepted")
    assert "You'll find it" not in sent[0]["text"]
    assert f"/library/book/{book_id}" in sent[0]["text"]


def test_private_book_is_named_but_not_linked(db, sent):
    book_id = db.create_book("Hidden Book")
    db.update_book(book_id, is_public=False)
    rec_id = _rec(db)
    db.update_recommendation(rec_id, {"book_id": book_id})
    recommendation_emails.send_status_change(db, rec_id, "accepted")
    assert "Hidden Book" in sent[0]["text"]
    assert "/library/book/" not in sent[0]["text"]


def test_accept_without_book_keeps_the_generic_email(db, api, sent):
    rec_id = _rec(db)
    tasks = _accept(api, rec_id)
    for t in tasks.tasks:
        t.func(*t.args, **t.kwargs)
    assert db.get_recommendation(rec_id)["book_id"] is None
    assert "Keep an eye on" in sent[0]["text"]


def test_linking_after_accept_sends_nothing_new(db, api, sent):
    rec_id = _rec(db)
    _accept(api, rec_id)
    book_id = db.create_book("Later Book")
    tasks = _accept(api, rec_id, book_id=book_id)
    assert tasks.tasks == []
    assert db.get_recommendation(rec_id)["book_title"] == "Later Book"


def test_unknown_book_is_refused_and_zero_unlinks(db, api, sent):
    rec_id = _rec(db)
    with pytest.raises(HTTPException) as exc:
        _accept(api, rec_id, book_id=999999)
    assert exc.value.status_code == 400
    assert db.get_recommendation(rec_id)["status"] == "new"

    book_id = db.create_book("Some Book")
    db.update_recommendation(rec_id, {"book_id": book_id})
    api.update_recommendation(rec_id, RecommendationUpdate(book_id=0), BackgroundTasks())
    assert db.get_recommendation(rec_id)["book_id"] is None
