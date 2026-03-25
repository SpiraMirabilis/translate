"""
WordPress / Fictioneer publishing endpoints.
"""
import os
import re
import threading
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from web.services.wp_client import WordPressClient, content_to_html, compute_hash

router = APIRouter(prefix="/api/wordpress")

_config = None
_db = None
_job_manager = None


def _persist_env(key: str, value: str):
    """Write or update a key=value in the .env file so it survives restarts."""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".env")
    env_path = os.path.normpath(env_path)

    lines = []
    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            lines = f.readlines()

    pattern = re.compile(rf"^{re.escape(key)}=")
    found = False
    for i, line in enumerate(lines):
        if pattern.match(line):
            lines[i] = f"{key}={value}\n"
            found = True
            break
    if not found:
        lines.append(f"{key}={value}\n")

    with open(env_path, "w") as f:
        f.writelines(lines)

# In-progress publish state
_publish_thread: Optional[threading.Thread] = None
_publish_cancel = threading.Event()


def init(config, entity_manager, job_manager):
    global _config, _db, _job_manager
    _config = config
    _db = entity_manager
    _job_manager = job_manager


def _get_client() -> WordPressClient:
    url = _config.wp_url
    user = _config.wp_username
    pw = _config.wp_app_password
    if not url or not user or not pw:
        raise HTTPException(status_code=400, detail="WordPress credentials not configured.")
    return WordPressClient(url, user, pw)


# ------------------------------------------------------------------
# Settings
# ------------------------------------------------------------------

@router.get("/settings")
async def get_wp_settings():
    return {
        "wp_url": _config.wp_url,
        "wp_username": _config.wp_username,
        "has_password": bool(_config.wp_app_password),
    }


class WpSettingsUpdate(BaseModel):
    wp_url: Optional[str] = None
    wp_username: Optional[str] = None
    wp_app_password: Optional[str] = None


@router.put("/settings")
async def update_wp_settings(req: WpSettingsUpdate):
    if req.wp_url is not None:
        _config.wp_url = req.wp_url.rstrip("/")
        os.environ["WP_URL"] = _config.wp_url
        _persist_env("WP_URL", _config.wp_url)
    if req.wp_username is not None:
        _config.wp_username = req.wp_username
        os.environ["WP_USERNAME"] = _config.wp_username
        _persist_env("WP_USERNAME", _config.wp_username)
    if req.wp_app_password is not None:
        _config.wp_app_password = req.wp_app_password
        os.environ["WP_APP_PASSWORD"] = _config.wp_app_password
        _persist_env("WP_APP_PASSWORD", _config.wp_app_password)
    return {"status": "ok"}


# ------------------------------------------------------------------
# Test connection
# ------------------------------------------------------------------

@router.post("/test")
async def test_wp_connection():
    try:
        client = _get_client()
        info = client.test_connection()
        return {"status": "ok", "site_name": info["name"], "site_url": info["url"]}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ------------------------------------------------------------------
# Book publish status
# ------------------------------------------------------------------

@router.get("/books/{book_id}/status")
async def get_book_publish_status(book_id: int):
    book = _db.get_book(book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found.")

    chapters = _db.list_chapters(book_id)
    wp_states = _db.get_all_wp_states(book_id)

    # Index states by chapter_number (None = story)
    state_map = {}
    for s in wp_states:
        state_map[s["chapter_number"]] = s

    story_state = state_map.get(None)
    chapter_statuses = []
    for ch in chapters:
        num = ch["chapter"]
        st = state_map.get(num)
        # Fetch full chapter to get content for hash
        full_ch = _db.get_chapter(book_id=book_id, chapter_number=num)
        content_lines = (full_ch.get("content") or []) if full_ch else []
        if isinstance(content_lines, str):
            content_lines = content_lines.split("\n")
        ch_title = ch.get("title") or (full_ch.get("title") if full_ch else "") or f"Chapter {num}"
        current_hash = compute_hash(content_lines, title=ch_title)

        if st is None:
            status = "new"
        elif st["content_hash"] == current_hash:
            status = "published"
        else:
            status = "changed"

        chapter_statuses.append({
            "chapter_number": num,
            "title": ch.get("title") or f"Chapter {num}",
            "status": status,
            "wp_post_id": st["wp_post_id"] if st else None,
        })

    return {
        "book_id": book_id,
        "book_title": book["title"],
        "story_published": story_state is not None,
        "story_wp_post_id": story_state["wp_post_id"] if story_state else None,
        "chapters": chapter_statuses,
        "is_publishing": _publish_thread is not None and _publish_thread.is_alive(),
    }


# ------------------------------------------------------------------
# Publish
# ------------------------------------------------------------------

class PublishChapterRequest(BaseModel):
    chapter_group: str = ""


@router.post("/books/{book_id}/chapters/{chapter_number}/publish")
async def publish_single_chapter(book_id: int, chapter_number: int, req: PublishChapterRequest):
    """Publish or update a single chapter to WordPress."""
    book = _db.get_book(book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found.")

    full_ch = _db.get_chapter(book_id=book_id, chapter_number=chapter_number)
    if not full_ch:
        raise HTTPException(status_code=404, detail="Chapter not found.")

    # Story must already exist on WP
    story_state = _db.get_wp_state(book_id, chapter_number=None)
    if not story_state:
        raise HTTPException(status_code=400, detail="Book has not been published to WordPress yet. Use the full publish from the Books page first.")

    client = _get_client()

    # Verify story still exists
    story_wp_id = story_state["wp_post_id"]
    existing_story = client.get_post("fcn_story", story_wp_id)
    if existing_story is None:
        _db.delete_wp_state_single(book_id, chapter_number=None)
        raise HTTPException(status_code=400, detail="Story post no longer exists on WordPress. Re-publish the full book first.")

    content_lines = (full_ch.get("content") or [])
    if isinstance(content_lines, str):
        content_lines = content_lines.split("\n")
    title = full_ch.get("title") or f"Chapter {chapter_number}"
    current_hash = compute_hash(content_lines, title=title)
    html = content_to_html(content_lines)

    ch_state = _db.get_wp_state(book_id, chapter_number)
    action = None

    try:
        if ch_state is None:
            # New chapter
            wp_id = client.create_chapter(title, html, story_wp_id, group=req.chapter_group)
            _db.save_wp_state(book_id, chapter_number, wp_id, "fcn_chapter", current_hash)
            action = "created"
        elif ch_state["content_hash"] == current_hash:
            action = "unchanged"
        else:
            # Changed — check if post still exists
            wp_id = ch_state["wp_post_id"]
            existing = client.get_post("fcn_chapter", wp_id)
            if existing is None:
                wp_id = client.create_chapter(title, html, story_wp_id, group=req.chapter_group)
                _db.save_wp_state(book_id, chapter_number, wp_id, "fcn_chapter", current_hash)
                action = "created"
            else:
                client.update_chapter(wp_id, title=title, html_content=html)
                _db.save_wp_state(book_id, chapter_number, wp_id, "fcn_chapter", current_hash)
                action = "updated"

        # Update story chapter list
        all_states = _db.get_all_wp_states(book_id)
        chapter_wp_ids = [s["wp_post_id"] for s in all_states if s["chapter_number"] is not None]
        if chapter_wp_ids:
            client.update_story(story_wp_id, chapter_ids=chapter_wp_ids)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"WordPress publish failed: {e}")

    # Activity log
    try:
        _job_manager.log_activity(
            "wordpress",
            f"Chapter {chapter_number} of \"{book['title']}\" {action} on WordPress",
            book_id=book_id,
            book_name=book["title"],
        )
    except Exception:
        pass

    wp_state = _db.get_wp_state(book_id, chapter_number)
    return {
        "status": "ok",
        "action": action,
        "wp_post_id": wp_state["wp_post_id"] if wp_state else None,
    }


class PublishRequest(BaseModel):
    story_status: str = "Ongoing"
    story_rating: str = "Everyone"
    chapter_group: str = ""


@router.post("/books/{book_id}/publish")
async def publish_book(book_id: int, req: PublishRequest):
    global _publish_thread

    if _publish_thread is not None and _publish_thread.is_alive():
        raise HTTPException(status_code=409, detail="A publish operation is already in progress.")

    book = _db.get_book(book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found.")

    _publish_cancel.clear()

    _publish_thread = threading.Thread(
        target=_publish_worker,
        args=(book_id, book, req.story_status, req.story_rating, req.chapter_group),
        daemon=True,
    )
    _publish_thread.start()
    return {"status": "started"}


@router.post("/books/{book_id}/cancel")
async def cancel_publish(book_id: int):
    _publish_cancel.set()
    return {"status": "cancelled"}


# ------------------------------------------------------------------
# Publish worker (background thread)
# ------------------------------------------------------------------

def _publish_worker(book_id: int, book: dict, story_status: str, story_rating: str, chapter_group: str):
    import traceback
    created = 0
    updated = 0
    skipped = 0
    errors = 0

    def send(msg: dict):
        _job_manager.send_message_sync(msg)

    try:
        url = _config.wp_url
        user = _config.wp_username
        pw = _config.wp_app_password
        print(f"[WP Publish] Starting publish for book {book_id}: '{book.get('title')}'")
        print(f"[WP Publish] WP URL: {url}, User: {user}, Has password: {bool(pw)}")
        if not url or not user or not pw:
            send({"type": "wp_publish", "step": "error", "error": "WordPress credentials not configured."})
            return
        client = WordPressClient(url, user, pw)
        chapter_list = _db.list_chapters(book_id)

        send({"type": "wp_publish", "step": "start", "total": len(chapter_list)})

        # --- Story ---
        story_state = _db.get_wp_state(book_id, chapter_number=None)
        story_wp_id = None
        existing_story = None

        if story_state:
            story_wp_id = story_state["wp_post_id"]
            # Check if story still exists on WP
            existing_story = client.get_post("fcn_story", story_wp_id)
            if existing_story is None:
                _db.delete_wp_state_single(book_id, chapter_number=None)
                story_state = None

        print(f"[WP Publish] Story state: {story_state}")
        if story_state is None:
            send({"type": "wp_publish", "step": "story", "action": "creating"})
            print(f"[WP Publish] Creating new story...")
            story_wp_id = client.create_story(
                title=book["title"],
                content=book.get("description", ""),
                status=story_status,
                rating=story_rating,
                short_description=book.get("description", ""),
            )
            _db.save_wp_state(book_id, None, story_wp_id, "fcn_story", "")
            print(f"[WP Publish] Story created with WP ID: {story_wp_id}")
            send({"type": "wp_publish", "step": "story", "action": "created", "wp_post_id": story_wp_id})
        else:
            client.update_story(
                story_wp_id, title=book["title"],
                status=story_status, rating=story_rating,
            )
            send({"type": "wp_publish", "step": "story", "action": "updated", "wp_post_id": story_wp_id})

        # --- Cover image as featured image (skip if already set) ---
        cover_rel = book.get("cover_image")
        if cover_rel and story_wp_id:
            # Check if the story already has a featured image
            has_cover = existing_story is not None and existing_story.get("featured_media", 0) > 0

            if not has_cover:
                cover_path = os.path.normpath(os.path.join(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", cover_rel
                ))
                if os.path.exists(cover_path):
                    try:
                        send({"type": "wp_publish", "step": "story", "action": "uploading cover"})
                        media_id = client.upload_media(cover_path)
                        client.set_featured_image("fcn_story", story_wp_id, media_id)
                        print(f"[WP Publish] Cover uploaded as media {media_id}")
                    except Exception as e:
                        print(f"[WP Publish] Warning: cover upload failed: {e}")
            else:
                print(f"[WP Publish] Cover already set, skipping upload")

        # --- Chapters ---
        print(f"[WP Publish] Processing {len(chapter_list)} chapters...")
        chapter_wp_ids = []
        for i, ch_meta in enumerate(chapter_list):
            if _publish_cancel.is_set():
                send({"type": "wp_publish", "step": "cancelled"})
                return

            num = ch_meta["chapter"]
            title = ch_meta.get("title") or f"Chapter {num}"
            full_ch = _db.get_chapter(book_id=book_id, chapter_number=num)
            content_lines = (full_ch.get("content") or []) if full_ch else []
            if isinstance(content_lines, str):
                content_lines = content_lines.split("\n")
            current_hash = compute_hash(content_lines, title=title)
            html = content_to_html(content_lines)

            send({
                "type": "wp_publish", "step": "chapter",
                "current": i + 1, "total": len(chapter_list), "title": title,
            })

            ch_state = _db.get_wp_state(book_id, num)

            try:
                if ch_state is None:
                    # New chapter
                    wp_id = client.create_chapter(title, html, story_wp_id, group=chapter_group)
                    _db.save_wp_state(book_id, num, wp_id, "fcn_chapter", current_hash)
                    chapter_wp_ids.append(wp_id)
                    created += 1
                elif ch_state["content_hash"] == current_hash:
                    # Unchanged
                    chapter_wp_ids.append(ch_state["wp_post_id"])
                    skipped += 1
                else:
                    # Changed — check if post still exists
                    wp_id = ch_state["wp_post_id"]
                    existing = client.get_post("fcn_chapter", wp_id)
                    if existing is None:
                        # Re-create
                        wp_id = client.create_chapter(title, html, story_wp_id, group=chapter_group)
                        _db.save_wp_state(book_id, num, wp_id, "fcn_chapter", current_hash)
                        created += 1
                    else:
                        client.update_chapter(wp_id, title=title, html_content=html)
                        _db.save_wp_state(book_id, num, wp_id, "fcn_chapter", current_hash)
                        updated += 1
                    chapter_wp_ids.append(wp_id)
            except Exception as e:
                errors += 1
                print(f"[WP Publish] Chapter {num} error: {e}")
                traceback.print_exc()
                send({
                    "type": "wp_publish", "step": "chapter_error",
                    "chapter": num, "title": title, "error": str(e),
                })
                # Keep going — already-published chapters are preserved
                if ch_state:
                    chapter_wp_ids.append(ch_state["wp_post_id"])

        # --- Update story chapter list ---
        if story_wp_id and chapter_wp_ids:
            try:
                client.update_story(story_wp_id, chapter_ids=chapter_wp_ids)
            except Exception as e:
                errors += 1
                send({"type": "wp_publish", "step": "chapter_error",
                      "chapter": 0, "title": "Story chapter list", "error": str(e)})

    except Exception as e:
        print(f"[WP Publish] FATAL ERROR: {e}")
        traceback.print_exc()
        send({"type": "wp_publish", "step": "error", "error": str(e)})
    finally:
        send({
            "type": "wp_publish", "step": "done",
            "created": created, "updated": updated,
            "skipped": skipped, "errors": errors,
        })

        # Activity log
        try:
            _job_manager.log_activity(
                "wordpress",
                f"Published \"{book['title']}\" to WordPress: {created} created, {updated} updated, {skipped} skipped, {errors} errors",
                book_id=book_id,
                book_name=book["title"],
            )
        except Exception:
            pass
