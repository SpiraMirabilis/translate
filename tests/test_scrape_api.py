"""Admin scraping endpoints: they only read state and queue rows for the worker."""
import json


def _book_with_toc(db, n=5):
    book_id = db.create_book("Scraped")
    db.update_book(book_id, source_url="https://twkan.com/book/9.html")
    toc = {"chapterCount": n, "chapters": [
        {"position": i, "number": i, "title": f"第{i}章 x", "url": f"https://twkan.com/txt/9/{i}.html"}
        for i in range(1, n + 1)]}
    db.update_book_scrape(book_id, last_toc=toc, toc_count=n)
    return book_id


def test_book_state_proposes_link_and_links(admin_client, db):
    book_id = _book_with_toc(db)
    for n in (1, 2, 3):
        db.add_to_queue(book_id=book_id, content=["x"], title=f"第{n}章 x", chapter_number=n)
    body = admin_client.get(f"/api/books/{book_id}/scrape").json()
    assert body["have_max"] == 3 and body["proposal"]["index"] == 2
    assert body["proposal"]["method"] == "number"  # every title is "x": no unique title match
    assert body["state"]["mode"] == "off" and len(body["toc"]) == 5

    r = admin_client.post(f"/api/books/{book_id}/scrape/link", json={"index": 2, "chapter_number": 3})
    assert r.status_code == 200 and r.json()["pending"] == 2
    state = db.get_book_scrape(book_id)
    assert state["linked"] == 1 and state["cursor_number"] == 3 and state["number_offset"] == 0


def test_mode_and_run_options(admin_client, db):
    book_id = _book_with_toc(db)
    r = admin_client.put(f"/api/books/{book_id}/scrape", json={
        "mode": "auto", "auto_gap": 5,
        "run_options": {"no_review": False, "save_as_draft": True}})
    assert r.status_code == 200
    state = db.get_book_scrape(book_id)
    assert state["mode"] == "auto" and state["auto_gap"] == 5
    assert state["run_options"] == {"translation_model": None, "no_review": False, "save_as_draft": True}
    assert admin_client.put(f"/api/books/{book_id}/scrape", json={"mode": "bogus"}).status_code == 400


def test_per_book_model_override(admin_client, db):
    book_id = _book_with_toc(db)
    r = admin_client.put(f"/api/books/{book_id}/scrape", json={
        "run_options": {"translation_model": " deepseek:deepseek-chat "}})
    assert r.status_code == 200
    assert r.json()["state"]["run_options"]["translation_model"] == "deepseek:deepseek-chat"
    assert r.json()["default_model"]   # the global default is reported alongside
    r = admin_client.put(f"/api/books/{book_id}/scrape", json={
        "run_options": {"translation_model": "not a model"}})
    assert r.status_code == 400
    r = admin_client.put(f"/api/books/{book_id}/scrape", json={"run_options": {"translation_model": ""}})
    assert r.json()["state"]["run_options"]["translation_model"] is None


def test_mode_requires_source_url(admin_client, db):
    book_id = db.create_book("No URL")
    r = admin_client.put(f"/api/books/{book_id}/scrape", json={"mode": "queue"})
    assert r.status_code == 400


def test_check_backfill_cancel_retry(admin_client, db):
    book_id = _book_with_toc(db)
    r = admin_client.post(f"/api/books/{book_id}/scrape/check", json={})
    assert r.status_code == 200
    assert admin_client.post(f"/api/books/{book_id}/scrape/check", json={}).status_code == 409

    r = admin_client.post(f"/api/books/{book_id}/scrape/backfill", json={"from_index": 0})
    assert r.status_code == 200
    task = db.get_scrape_task(r.json()["task_id"])
    assert task["kind"] == "chapters" and task["from_url"].endswith("/1.html")
    assert db.get_book_scrape(book_id)["linked"] == 1  # empty book: linked before ch1

    r = admin_client.post(f"/api/scrape/tasks/{task['id']}/cancel")
    assert r.json()["status"] == "cancelled"
    r = admin_client.post(f"/api/scrape/tasks/{task['id']}/retry")
    assert r.json()["status"] == "queued"

    overview = admin_client.get("/api/scrape/overview").json()
    assert {t["id"] for t in overview["queued"]} >= {task["id"]}
    assert any(b["book_id"] == book_id for b in overview["books"])
    assert admin_client.get("/api/scrape/count").json()["count"] == 0


def test_migration_26_mysql_ddl_is_mysql():
    """Migration 26's MySQL statements must be the MySQL baseline DDL, not the
    SQLite one — the SQLite copy took both processes down on MariaDB once
    (AUTOINCREMENT), and the suite runs on SQLite so nothing else catches it."""
    import re
    import db_backend
    from db.migrations import _SCRAPE_DDL

    def norm(sql):
        return re.sub(r"\s+", " ", sql).strip()
    baseline = {norm(d) for d in db_backend._COMMON_DDL_MYSQL}
    for ddl in _SCRAPE_DDL["mysql"]:
        assert "AUTOINCREMENT" not in ddl and "ENGINE=InnoDB" in ddl
        assert norm(ddl) in baseline


def test_book_state_reports_site_support(admin_client, db, tmp_path, monkeypatch):
    import scrape_sync
    monkeypatch.setattr(scrape_sync, "SITES_CACHE", str(tmp_path / "sites.json"))
    book_id = _book_with_toc(db)
    assert admin_client.get(f"/api/books/{book_id}/scrape").json()["site_supported"] is None
    scrape_sync.save_supported_sites(["69shuba.com"], "2026-10-26T00:00:00")
    assert admin_client.get(f"/api/books/{book_id}/scrape").json()["site_supported"] is False
    overview = admin_client.get("/api/scrape/overview").json()
    assert overview["supported_sites"]["sites"] == ["69shuba.com"]
    assert next(b for b in overview["books"] if b["book_id"] == book_id)["site_supported"] is False


def _signed(secret, body, t=None):
    import hashlib
    import hmac
    import time
    t = int(time.time()) if t is None else t
    sig = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={t},v1={sig}"


def test_scraper_notify_logs_a_red_line_with_the_novnc_link(api_client, db, monkeypatch):
    import scrape_sync
    monkeypatch.setattr(scrape_sync, "notify_secret", lambda: "s3cret")
    book_id = _book_with_toc(db)
    task_id = db.create_scrape_task(book_id, "chapters", reason="catchup", priority=20)
    db.update_scrape_task(task_id, status="running", scraper_job_id="job-abc")
    body = json.dumps({"title": "Scraper blocked (twkan)", "message": "…", "jobId": "job-abc",
                       "site": "twkan",
                       "vncUrl": "http://100.65.58.103:6080/vnc.html?autoconnect=1"}).encode()

    # api_client carries no session cookie: the signature is the only credential.
    r = api_client.post("/api/scraper-notify", content=body,
                        headers={"content-type": "application/json",
                                 "X-Scraper-Signature": _signed("s3cret", body)})
    assert r.status_code == 200 and r.json()["book_id"] == book_id
    entry = db.get_activity_log(limit=5)[-1]
    assert entry["type"] == "scrape_blocked"
    assert entry["message"].startswith("Scrape (Scraped): blocked on a Cloudflare challenge at twkan")
    assert entry["entities"] == [{"label": "Solve it in noVNC",
                                  "href": "http://100.65.58.103:6080/vnc.html?autoconnect=1"}]
    assert db.get_scrape_task(task_id)["status"] == "blocked"


def test_scraper_notify_rejects_bad_stale_or_unsigned_posts(api_client, monkeypatch):
    import scrape_sync
    monkeypatch.setattr(scrape_sync, "notify_secret", lambda: "s3cret")
    body = b'{"jobId": "x"}'
    post = lambda headers: api_client.post("/api/scraper-notify", content=body, headers=headers)  # noqa: E731
    assert post({}).status_code == 401
    assert post({"X-Scraper-Signature": _signed("wrong", body)}).status_code == 401
    assert post({"X-Scraper-Signature": _signed("s3cret", body, t=1)}).status_code == 401   # stale
    monkeypatch.setattr(scrape_sync, "notify_secret", lambda: "")
    assert post({"X-Scraper-Signature": _signed("", body)}).status_code == 401   # no secret configured


def test_scraper_notify_falls_back_to_the_setting_then_no_link(api_client, db, monkeypatch):
    import scrape_sync
    monkeypatch.setattr(scrape_sync, "notify_secret", lambda: "s3cret")
    settings = {"scraper_vnc_url": "http://fallback:6080/vnc.html"}
    monkeypatch.setattr(scrape_sync, "setting", lambda k: settings.get(k, scrape_sync.DEFAULTS[k]))
    body = json.dumps({"jobId": "unknown-job", "site": "69shuba"}).encode()   # no vncUrl sent
    r = api_client.post("/api/scraper-notify", content=body,
                        headers={"X-Scraper-Signature": _signed("s3cret", body)})
    assert r.status_code == 200
    assert db.get_activity_log(limit=5)[-1]["entities"] == [
        {"label": "Solve it in noVNC", "href": "http://fallback:6080/vnc.html"}]
    settings["scraper_vnc_url"] = ""                       # neither: logged, no link
    r = api_client.post("/api/scraper-notify", content=body,
                        headers={"X-Scraper-Signature": _signed("s3cret", body)})
    assert r.status_code == 200 and not db.get_activity_log(limit=5)[-1]["entities"]
    body = json.dumps({"jobId": "unknown-job", "site": "69shuba"}).encode()
    r = api_client.post("/api/scraper-notify", content=body,
                        headers={"X-Scraper-Signature": _signed("s3cret", body)})
    assert r.status_code == 200
    entry = db.get_activity_log(limit=5)[-1]
    assert entry["type"] == "scrape_blocked" and not entry["entities"]


def test_resolve_unplaced_endpoint(admin_client, db):
    book_id = _book_with_toc(db)
    db.set_unplaced(book_id, [{"id": "abc", "title": "小结", "url": "u", "after": 2,
                               "status": "pending", "text": "回顾"}])
    body = admin_client.get(f"/api/books/{book_id}/scrape").json()
    assert [i["id"] for i in body["unplaced"]] == ["abc"]
    assert admin_client.get("/api/scrape/count").json()["count"] == 0     # mode off: no badge
    r = admin_client.post(f"/api/books/{book_id}/scrape/unplaced/abc", json={"action": "append"})
    assert r.status_code == 400                                          # no ch2 queued or saved
    r = admin_client.post(f"/api/books/{book_id}/scrape/unplaced/abc", json={"action": "discard"})
    assert r.status_code == 200 and r.json()["done"] == "discarded"
    body = admin_client.get(f"/api/books/{book_id}/scrape").json()
    assert body["unplaced"] == [] and body["unplaced_recent"][0]["status"] == "discard"
    assert admin_client.post(f"/api/books/{book_id}/scrape/unplaced/abc",
                             json={"action": "discard"}).status_code == 404
