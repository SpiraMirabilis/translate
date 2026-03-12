"""
WordPress REST API client for publishing to Fictioneer.
"""
import hashlib
import os
import httpx


def content_to_html(content_lines: list[str]) -> str:
    """Convert a list of paragraph strings to HTML <p> blocks."""
    parts = []
    for line in content_lines:
        line = line.strip()
        if line:
            parts.append(f"<p>{line}</p>")
    return "\n".join(parts)


def compute_hash(content_lines: list[str]) -> str:
    """SHA-256 hash of content lines for change detection."""
    raw = "\n".join(content_lines)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class WordPressClient:
    """Interact with WordPress + Fictioneer via the WP REST API."""

    def __init__(self, wp_url: str, username: str, app_password: str):
        self.wp_url = wp_url.rstrip("/")
        self.base = f"{self.wp_url}/wp-json/wp/v2"
        self.t9_base = f"{self.wp_url}/wp-json/t9/v1"
        self.auth = (username, app_password)
        self.client = httpx.Client(timeout=30)

    def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        resp = self.client.request(method, url, auth=self.auth, **kwargs)
        if resp.status_code >= 400:
            print(f"[WP Client] {method} {url} -> {resp.status_code}: {resp.text[:500]}")
        resp.raise_for_status()
        return resp

    def test_connection(self) -> dict:
        """GET /wp-json/ — returns site info if credentials work."""
        resp = self.client.get(
            f"{self.wp_url}/wp-json/",
            auth=self.auth,
        )
        resp.raise_for_status()
        data = resp.json()
        return {"name": data.get("name", ""), "url": data.get("url", "")}

    # ------------------------------------------------------------------
    # Stories (fcn_story)
    # ------------------------------------------------------------------

    def create_story(
        self,
        title: str,
        content: str = "",
        status: str = "Ongoing",
        rating: str = "Everyone",
        short_description: str = "",
    ) -> int:
        """Create an fcn_story post and set Fictioneer meta. Returns WP post ID."""
        payload = {
            "title": title,
            "content": content,
            "status": "publish",
        }
        resp = self._request("POST", f"{self.base}/fcn_story", json=payload)
        wp_id = resp.json()["id"]

        # Set Fictioneer meta via our custom endpoint (non-fatal)
        try:
            self._request("POST", f"{self.t9_base}/story/{wp_id}/set-meta", json={
                "status": status,
                "rating": rating,
                "short_description": short_description,
            })
        except Exception as e:
            print(f"[WP Client] Warning: set-meta failed for story {wp_id}: {e}")

        return wp_id

    def update_story(
        self,
        wp_post_id: int,
        title: str | None = None,
        chapter_ids: list[int] | None = None,
        status: str | None = None,
        rating: str | None = None,
        content: str | None = None,
    ) -> dict:
        """Update an existing fcn_story post."""
        # Update basic post fields via standard REST
        payload: dict = {}
        if title is not None:
            payload["title"] = title
        if content is not None:
            payload["content"] = content
        if payload:
            self._request("POST", f"{self.base}/fcn_story/{wp_post_id}", json=payload)

        # Update Fictioneer meta via custom endpoint
        meta: dict = {}
        if status is not None:
            meta["status"] = status
        if rating is not None:
            meta["rating"] = rating
        if meta:
            self._request("POST", f"{self.t9_base}/story/{wp_post_id}/set-meta", json=meta)

        # Set chapter ordering via custom endpoint
        if chapter_ids is not None:
            resp = self._request("POST", f"{self.t9_base}/story/{wp_post_id}/set-chapters", json={
                "chapter_ids": chapter_ids,
            })
            return resp.json()

        return {"status": "ok"}

    # ------------------------------------------------------------------
    # Chapters (fcn_chapter)
    # ------------------------------------------------------------------

    def create_chapter(
        self,
        title: str,
        html_content: str,
        story_wp_id: int,
        group: str = "",
    ) -> int:
        """Create an fcn_chapter post and link to story. Returns WP post ID."""
        payload = {
            "title": title,
            "content": html_content,
            "status": "publish",
        }
        resp = self._request("POST", f"{self.base}/fcn_chapter", json=payload)
        wp_id = resp.json()["id"]

        # Link chapter to story via custom endpoint
        self._request("POST", f"{self.t9_base}/chapter/{wp_id}/link-story", json={
            "story_id": story_wp_id,
            "group": group,
        })

        return wp_id

    def update_chapter(
        self,
        wp_post_id: int,
        title: str | None = None,
        html_content: str | None = None,
    ) -> dict:
        """Update an existing fcn_chapter post."""
        payload: dict = {}
        if title is not None:
            payload["title"] = title
        if html_content is not None:
            payload["content"] = html_content
        resp = self._request("POST", f"{self.base}/fcn_chapter/{wp_post_id}", json=payload)
        return resp.json()

    # ------------------------------------------------------------------
    # Media
    # ------------------------------------------------------------------

    def upload_media(self, file_path: str, filename: str = None) -> int:
        """Upload a media file to WP. Returns the WP media attachment ID."""
        import mimetypes
        if not filename:
            filename = os.path.basename(file_path)
        mime_type = mimetypes.guess_type(filename)[0] or "image/jpeg"
        with open(file_path, "rb") as f:
            data = f.read()
        headers = {
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Type": mime_type,
        }
        resp = self._request("POST", f"{self.base}/media", content=data, headers=headers)
        return resp.json()["id"]

    def set_featured_image(self, post_type: str, wp_post_id: int, media_id: int) -> dict:
        """Set the featured image (thumbnail) on a post."""
        resp = self._request("POST", f"{self.base}/{post_type}/{wp_post_id}", json={
            "featured_media": media_id,
        })
        return resp.json()

    # ------------------------------------------------------------------
    # Generic
    # ------------------------------------------------------------------

    def get_post(self, post_type: str, wp_post_id: int) -> dict | None:
        """Fetch a single post by type and ID. Returns None on 404."""
        try:
            resp = self._request("GET", f"{self.base}/{post_type}/{wp_post_id}")
            return resp.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise
