"""Outbound email log + bounce ingestion.

No mail is sent: email_sender.send_email is stubbed per test. What these cover
is that every send path writes an email_log row with a Message-ID, and that a
bounce only suppresses an address when it quotes a Message-ID we logged for it.
"""
import importlib.util
import os
from email.message import EmailMessage
from types import SimpleNamespace

import pytest

from web.api import mail_ingest
from web.services import email_sender, recommendation_emails

TOKEN = "test-ingest-token"


@pytest.fixture
def sent(monkeypatch):
    """Capture send_email calls instead of talking to Postfix."""
    calls = []

    def fake_send(to_addr, subject, text_body, html_body=None, extra_headers=None):
        calls.append({"to": to_addr, "subject": subject, "headers": dict(extra_headers or {})})
        return (True, "ok")

    monkeypatch.setattr(email_sender, "send_email", fake_send)
    monkeypatch.setenv("EMAIL_FROM", "editor@example.com")
    monkeypatch.setenv("SECRET_KEY", "test-secret")  # unsubscribe/reply tokens
    return calls


@pytest.fixture
def ingest(db, monkeypatch):
    monkeypatch.setenv("MAIL_INGEST_TOKEN", TOKEN)
    mail_ingest.init(db)
    request = SimpleNamespace(headers={"X-Ingest-Token": TOKEN})

    def post(original_message_id, recipients):
        payload = mail_ingest.IngestBounce(
            original_message_id=original_message_id,
            recipients=[mail_ingest.BounceRecipient(**r) for r in recipients])
        return mail_ingest.ingest_bounce(payload, request)["results"]
    return post


def _rec(db, email="Reader@Example.org"):
    return db.create_recommendation({
        "novel_title": "Some Novel",
        "source_url": "https://example.com/novel",
        "source_language": "zh",
        "requester_name": "reader",
        "requester_email": email,
    })


def test_recommendation_email_is_logged_with_message_id(db, sent):
    rec_id = _rec(db)
    ok, _ = recommendation_emails.send_submit_confirmation(db, rec_id)
    assert ok

    [row] = db.list_email_log(recommendation_id=rec_id)
    assert row["recipient"] == "reader@example.org"
    assert row["kind"] == "rec_confirm"
    assert row["status"] == "sent"
    # The logged Message-ID is the one that actually went out.
    assert row["message_id"] == sent[0]["headers"]["Message-ID"]
    assert row["message_id"].startswith(f"<rec{rec_id}.")

    rec = db.get_recommendation(rec_id)
    assert rec["email_count"] == 1
    assert rec["bounced_email_count"] == 0
    assert rec["email_suppressed_reason"] is None


def test_status_change_and_admin_message_kinds(db, sent):
    rec_id = _rec(db)
    recommendation_emails.send_status_change(db, rec_id, "accepted")
    recommendation_emails.send_admin_message(db, rec_id, "Which raws are you reading?")
    kinds = sorted(r["kind"] for r in db.list_email_log(recommendation_id=rec_id))
    assert kinds == ["rec_accepted", "rec_message"]


def test_suppressed_send_is_logged_but_not_sent(db, sent):
    rec_id = _rec(db)
    db.add_email_suppression("reader@example.org", reason="unsubscribe")
    ok, detail = recommendation_emails.send_admin_message(db, rec_id, "hello")
    assert (ok, detail) == (False, "suppressed")
    assert sent == []
    [row] = db.list_email_log(recommendation_id=rec_id)
    assert row["status"] == "suppressed"
    assert db.get_recommendation(rec_id)["email_suppressed_reason"] == "unsubscribe"


def test_failed_send_is_logged(db, monkeypatch):
    monkeypatch.setenv("EMAIL_FROM", "editor@example.com")
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setattr(email_sender, "send_email",
                        lambda *a, **k: (False, "connection refused"))
    rec_id = _rec(db)
    recommendation_emails.send_submit_confirmation(db, rec_id)
    [row] = db.list_email_log(recommendation_id=rec_id)
    assert row["status"] == "failed"
    assert row["detail"] == "connection refused"


def test_send_logged_mints_message_id(db, sent):
    ok, _ = email_sender.send_logged(db, "comment_reply", "a@example.org", "Re", "body",
                                     comment_id=42)
    assert ok
    [row] = db.list_email_log(recipient="a@example.org")
    assert row["comment_id"] == 42
    assert row["message_id"] == sent[0]["headers"]["Message-ID"]
    assert row["message_id"].endswith("@example.com>")


def test_logging_failure_never_blocks_send(sent):
    broken_db = SimpleNamespace()  # no log_email attribute at all
    ok, _ = email_sender.send_logged(broken_db, "comment_reply", "a@example.org", "Re", "b")
    assert ok and len(sent) == 1


def test_hard_bounce_marks_send_and_suppresses(db, sent, ingest):
    rec_id = _rec(db)
    recommendation_emails.send_submit_confirmation(db, rec_id)
    mid = db.list_email_log(recommendation_id=rec_id)[0]["message_id"]

    [res] = ingest(mid, [{"email": "reader@example.org", "action": "failed",
                          "status": "5.1.1", "diagnostic": "smtp; 550 no such user"}])
    assert res == {"email": "reader@example.org", "matched": True, "hard": True,
                   "suppressed": True}

    [row] = db.list_email_log(recommendation_id=rec_id)
    assert row["status"] == "bounced"
    assert row["bounce_status"] == "5.1.1"
    assert "no such user" in row["bounce_diagnostic"]
    assert db.is_email_suppressed("reader@example.org")
    rec = db.get_recommendation(rec_id)
    assert rec["bounced_email_count"] == 1
    assert rec["email_suppressed_reason"] == "bounce"

    # The next send is blocked and logged as such.
    ok, detail = recommendation_emails.send_admin_message(db, rec_id, "hi")
    assert (ok, detail) == (False, "suppressed")


def test_forged_bounce_suppresses_nothing(db, sent, ingest):
    """Unknown Message-ID, or a real one for a different recipient."""
    rec_id = _rec(db)
    recommendation_emails.send_submit_confirmation(db, rec_id)
    mid = db.list_email_log(recommendation_id=rec_id)[0]["message_id"]

    [res] = ingest("<guess@example.com>", [{"email": "reader@example.org",
                                             "action": "failed", "status": "5.1.1"}])
    assert res["matched"] is False and res["suppressed"] is False
    [res] = ingest(mid, [{"email": "victim@example.org", "action": "failed",
                          "status": "5.1.1"}])
    assert res["matched"] is False and res["suppressed"] is False

    assert not db.is_email_suppressed("reader@example.org")
    assert not db.is_email_suppressed("victim@example.org")
    assert db.list_email_log(recommendation_id=rec_id)[0]["status"] == "sent"


def test_soft_bounce_is_recorded_not_suppressed(db, sent, ingest):
    rec_id = _rec(db)
    recommendation_emails.send_submit_confirmation(db, rec_id)
    mid = db.list_email_log(recommendation_id=rec_id)[0]["message_id"]

    [res] = ingest(mid, [{"email": "reader@example.org", "action": "failed",
                          "status": "4.4.1", "diagnostic": "connection timed out"}])
    assert res["matched"] and not res["hard"] and not res["suppressed"]
    assert db.list_email_log(recommendation_id=rec_id)[0]["status"] == "bounced"
    assert not db.is_email_suppressed("reader@example.org")


def test_delayed_notice_is_ignored(db, sent, ingest):
    rec_id = _rec(db)
    recommendation_emails.send_submit_confirmation(db, rec_id)
    mid = db.list_email_log(recommendation_id=rec_id)[0]["message_id"]
    assert ingest(mid, [{"email": "reader@example.org", "action": "delayed",
                         "status": "4.4.1"}]) == []
    assert db.list_email_log(recommendation_id=rec_id)[0]["status"] == "sent"


def test_bounce_ingest_requires_token(db, monkeypatch):
    from fastapi import HTTPException
    monkeypatch.setenv("MAIL_INGEST_TOKEN", TOKEN)
    mail_ingest.init(db)
    with pytest.raises(HTTPException) as exc:
        mail_ingest.ingest_bounce(mail_ingest.IngestBounce(),
                                  SimpleNamespace(headers={"X-Ingest-Token": "nope"}))
    assert exc.value.status_code == 401


# ------------------------------------------------------------------
# Mail monitor DSN parsing (deploy/t9_mail_monitor.py is stdlib-only and
# not a package, so it is loaded by path).
# ------------------------------------------------------------------

@pytest.fixture
def monitor(tmp_path, monkeypatch):
    monkeypatch.setenv("MAILMON_LOG", str(tmp_path / "mm.log"))
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "deploy", "t9_mail_monitor.py")
    spec = importlib.util.spec_from_file_location("t9_mail_monitor_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _postfix_dsn(original_mid, recipient, status="5.1.1", action="failed"):
    """A bounce shaped like Postfix's: report + delivery-status + returned message."""
    import email
    original = EmailMessage()
    original["From"] = "editor@example.com"
    original["To"] = recipient
    original["Subject"] = "We received your translation request"
    original["Message-ID"] = original_mid
    original.set_content("hello")
    raw = (
        "From: Mail Delivery System <MAILER-DAEMON@mx.example.com>\n"
        "To: editor@example.com\n"
        "Subject: Undelivered Mail Returned to Sender\n"
        "Message-ID: <dsn1@mx.example.com>\n"
        "MIME-Version: 1.0\n"
        'Content-Type: multipart/report; report-type=delivery-status; boundary="B"\n'
        "\n--B\nContent-Type: text/plain\n\nI'm sorry to have to inform you...\n"
        "\n--B\nContent-Type: message/delivery-status\n\n"
        "Reporting-MTA: dns; mx.example.com\n"
        "X-Postfix-Queue-ID: ABC123\n\n"
        f"Final-Recipient: rfc822; {recipient}\n"
        f"Original-Recipient: rfc822;{recipient}\n"
        f"Action: {action}\n"
        f"Status: {status}\n"
        "Diagnostic-Code: smtp; 550-5.1.1 The email account that you tried to reach\n"
        "    does not exist.\n"
        "\n--B\nContent-Type: message/rfc822\n\n"
        f"{original.as_string()}\n"
        "--B--\n"
    )
    return email.message_from_string(raw)


def test_monitor_parses_postfix_dsn(monitor):
    msg = _postfix_dsn("<rec7.abc@example.com>", "gone@example.org")
    assert monitor._is_dsn(msg)
    parsed = monitor._parse_dsn(msg)
    assert parsed["original_message_id"] == "<rec7.abc@example.com>"
    assert parsed["recipients"] == [{
        "email": "gone@example.org",
        "action": "failed",
        "status": "5.1.1",
        "diagnostic": "smtp; 550-5.1.1 The email account that you tried to reach does not exist.",
    }]


def test_monitor_does_not_treat_replies_as_dsn(monitor):
    msg = EmailMessage()
    msg["From"] = "reader@example.org"
    msg["Subject"] = "Re: your request"
    msg.set_content("thanks!")
    assert not monitor._is_dsn(msg)
