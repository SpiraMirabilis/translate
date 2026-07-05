"""
FB2 Processing Module for Translator Application.

Extracts chapters from FB2 (FictionBook 2.0) files — the Russian XML-based
e-book format — and adds them to the translation queue. Mirrors the public
interface of ``epub_processor.EPUBProcessor`` so it can be wired into the same
CLI / web-upload flows.
"""
import os
import re
import base64
import zipfile

from lxml import etree

# Chapter-number parsing is format-agnostic — reuse the EPUB helper, which
# handles Arabic ('Chapter 12', 'Глава 1') and Chinese-numeral forms and falls
# back to a sequential index for anything else (e.g. spelled-out Russian
# numerals like 'Глава первая').
from epub_processor import chapter_number_from_title
from illustrations import IllustrationCollector, make_marker, store_chapter_illustrations


# Prose elements (FB2 local names) whose text we keep when flattening a
# section to plain text. Anything not listed (notably nested <section> and the
# <title>) is skipped so recursion doesn't duplicate sub-section prose.
_PROSE_TAGS = {
    "p", "subtitle", "cite", "text-author", "v", "th", "td", "code",
}


class FB2Processor:
    """Process FB2 files, extract chapters, and add them to the queue."""

    def __init__(self, config, logger, db_manager):
        """
        Args:
            config: TranslationConfig object with script_dir and other settings
            logger: Logger object for logging messages
            db_manager: DatabaseManager instance for queue operations
        """
        self.config = config
        self.logger = logger
        self.db_manager = db_manager
        # Per-run illustration state; populated by process_fb2 before extraction.
        self._collector = None
        self._binary_map = {}

    # -- loading -----------------------------------------------------------

    def _read_bytes(self, fb2_path):
        """Return the raw FB2 XML bytes, transparently unzipping .fb2.zip/.zip.

        FB2 is frequently distributed zipped (one .fb2 entry per archive).
        Parsing from bytes (rather than a decoded str) lets lxml honour the
        document's own ``<?xml encoding=...?>`` declaration, which is commonly
        windows-1251 for Russian sources.
        """
        if zipfile.is_zipfile(fb2_path):
            with zipfile.ZipFile(fb2_path) as zf:
                names = [n for n in zf.namelist() if n.lower().endswith(".fb2")]
                if not names:
                    # Fall back to the first non-directory entry
                    names = [n for n in zf.namelist() if not n.endswith("/")]
                if not names:
                    raise ValueError("Zip archive contains no FB2 file")
                return zf.read(names[0])
        with open(fb2_path, "rb") as f:
            return f.read()

    def load_fb2(self, fb2_path):
        """Load and parse an FB2 file.

        Returns the root element with namespaces stripped (every element tag
        reduced to its local name) so downstream finds are simple and robust to
        the various namespace URIs FB2 files use in the wild. Returns None on
        failure.
        """
        try:
            raw = self._read_bytes(fb2_path)
            # recover=True tolerates the malformed entities / stray bytes common
            # in scraped FB2 files.
            parser = etree.XMLParser(recover=True, huge_tree=True)
            root = etree.fromstring(raw, parser=parser)
            if root is None:
                self.logger.error(f"Failed to parse FB2 (empty tree): {fb2_path}")
                return None

            # Strip element namespaces so we can use bare local-name finds.
            for el in root.iter():
                if isinstance(el.tag, str):
                    el.tag = etree.QName(el).localname
            etree.cleanup_namespaces(root)

            self.logger.info(f"Successfully loaded FB2: {os.path.basename(fb2_path)}")
            return root
        except Exception as e:
            self.logger.error(f"Failed to load FB2 {fb2_path}: {e}")
            return None

    # -- metadata ----------------------------------------------------------

    def get_fb2_metadata(self, fb2_path):
        """Extract title/author/language/publisher from an FB2 file.

        Returns a dict with the same shape as ``EPUBProcessor.get_epub_metadata``.
        """
        try:
            root = self.load_fb2(fb2_path)
            if root is None:
                return {"title": None, "author": None}

            metadata = {
                "title": None,
                "author": None,
                "language": None,
                "publisher": None,
            }

            title_info = root.find(".//description/title-info")
            if title_info is not None:
                book_title = title_info.find("book-title")
                if book_title is not None and book_title.text:
                    metadata["title"] = book_title.text.strip()

                author = title_info.find("author")
                if author is not None:
                    metadata["author"] = self._format_author(author)

                lang = title_info.find("lang")
                if lang is not None and lang.text:
                    metadata["language"] = lang.text.strip()

            publish_info = root.find(".//description/publish-info")
            if publish_info is not None:
                publisher = publish_info.find("publisher")
                if publisher is not None and publisher.text:
                    metadata["publisher"] = publisher.text.strip()

            self.logger.info(
                f"Extracted metadata: title='{metadata['title']}', author='{metadata['author']}'"
            )
            return metadata
        except Exception as e:
            self.logger.error(f"Error extracting FB2 metadata: {e}")
            return {"title": None, "author": None}

    def _format_author(self, author_el):
        """Build a display name from an FB2 <author> element.

        Prefers structured <first-name>/<middle-name>/<last-name>, falling back
        to <nickname>.
        """
        parts = []
        for tag in ("first-name", "middle-name", "last-name"):
            child = author_el.find(tag)
            if child is not None and child.text and child.text.strip():
                parts.append(child.text.strip())
        if parts:
            return " ".join(parts)

        nickname = author_el.find("nickname")
        if nickname is not None and nickname.text and nickname.text.strip():
            return nickname.text.strip()
        return None

    # -- chapter extraction ------------------------------------------------

    def extract_chapters(self, root):
        """Extract chapters from the parsed FB2 tree.

        FB2 bodies contain nested <section> elements; the natural mapping is one
        chapter per leaf-ish section. Sections that only contain sub-sections
        (e.g. a Volume wrapping Chapters) are recursed into rather than emitted,
        so the result is a flat chapter list regardless of nesting depth.

        Returns a list of dicts: {title, content, number}.
        """
        chapters = []

        # Index <binary> blobs by id so inline/block <image> refs resolve.
        self._binary_map = {
            b.get("id"): b for b in root.findall("binary") if b.get("id")
        }

        # Use the main body only; an FB2 may carry extra bodies (notably
        # name="notes"/"comments" footnote bodies) we don't want as chapters.
        bodies = [
            b for b in root.findall("body")
            if (b.get("name") or "").lower() not in ("notes", "comments", "footnotes")
        ]
        if not bodies:
            bodies = root.findall("body")  # fall back to whatever exists

        raw_chapters = []
        for body in bodies:
            for section in body.findall("section"):
                self._collect_sections(section, raw_chapters)
            # Some FB2 files put prose paragraphs directly under <body> with no
            # wrapping <section>. Capture that as a single chapter.
            if not body.findall("section"):
                text = self._section_text(body)
                if text.strip():
                    raw_chapters.append((self._section_title(body), text))

        for i, (title, content) in enumerate(raw_chapters, 1):
            content = content.strip()
            # skip empty / title-only sections, but keep image-only chapters
            if not content or (len(content) < 50 and "⟦IMG:" not in content):
                continue

            if not title:
                title = f"Chapter {i}"
            chapter_number = chapter_number_from_title(title, default=i)

            chapters.append({
                "title": title,
                "content": content,
                "number": chapter_number,
            })

        # Preserve document order; only re-sort when titles yield a clean,
        # unique numbering (otherwise the sequential fallback already matches
        # document order).
        numbers = [c["number"] for c in chapters]
        if len(set(numbers)) == len(numbers):
            chapters.sort(key=lambda x: x["number"])

        self.logger.info(f"Extracted {len(chapters)} chapters")
        return chapters

    def _collect_sections(self, section, out):
        """Recursively gather (title, content) chapter tuples from a section.

        A section with child sections is treated as a container and recursed
        into; a leaf section is emitted as a chapter.
        """
        subsections = section.findall("section")
        if subsections:
            # If the container itself has direct prose (intro text before its
            # sub-sections), emit that as its own chapter so it isn't lost.
            own_text = self._section_text(section)
            if own_text.strip():
                out.append((self._section_title(section), own_text))
            for sub in subsections:
                self._collect_sections(sub, out)
        else:
            out.append((self._section_title(section), self._section_text(section)))

    def _section_title(self, section):
        """Join the text of a section's <title> into a single line."""
        title_el = section.find("title")
        if title_el is None:
            return None
        text = " ".join(t.strip() for t in title_el.itertext() if t and t.strip())
        return re.sub(r"\s+", " ", text).strip() or None

    def _section_text(self, section):
        """Flatten a section's own prose to text, excluding nested sections.

        Only direct children are walked; <section> and <title> children are
        skipped (titles are handled separately, sub-sections via recursion).
        Each prose block becomes a line; <empty-line/> yields a blank line.
        """
        lines = []
        for child in section:
            tag = child.tag if isinstance(child.tag, str) else ""
            if tag in ("section", "title"):
                continue
            if tag == "empty-line":
                lines.append("")
                continue
            if tag == "image":
                # Block-level illustration between paragraphs.
                self._emit_images(child, lines)
                continue
            if tag in ("poem", "epigraph", "annotation", "table"):
                # Block containers — pull all descendant prose text out.
                for line in self._block_lines(child):
                    lines.append(line)
                self._emit_images(child, lines)
                continue
            if tag in _PROSE_TAGS:
                text = self._element_text(child)
                if text:
                    lines.append(text)
                # Inline <image> inside a paragraph → marker after the text.
                self._emit_images(child, lines)
        # Collapse 3+ blank lines down to a single blank separator.
        text = "\n".join(lines)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text

    def _block_lines(self, element):
        """Yield one text line per prose descendant of a block container."""
        for el in element.iter():
            tag = el.tag if isinstance(el.tag, str) else ""
            if tag in _PROSE_TAGS:
                text = self._element_text(el)
                if text:
                    yield text

    def _emit_images(self, element, lines):
        """Append an illustration marker line for each <image> in `element`.

        `element.iter()` includes `element` itself, so this handles both a
        block-level <image> and inline <image>s nested in a paragraph/block.
        No-op when no collector is active (e.g. direct extract_chapters calls).
        """
        if self._collector is None:
            return
        for el in element.iter():
            if (el.tag if isinstance(el.tag, str) else "") != "image":
                continue
            data, mime = self._resolve_image(el)
            if not data:
                continue
            marker_id = self._collector.add(
                data, mime=mime, original_href=self._href(el)
            )
            if marker_id:
                lines.append(make_marker(marker_id))

    def _resolve_image(self, image_el):
        """Resolve an <image> ref to (bytes, mime) via the binary map."""
        href = self._href(image_el)
        if not href:
            return None, None
        binary = self._binary_map.get(href.lstrip("#"))
        if binary is None or not (binary.text and binary.text.strip()):
            return None, None
        mime = (binary.get("content-type") or "image/jpeg").strip().lower()
        try:
            return base64.b64decode(binary.text.strip()), mime
        except Exception:
            return None, None

    def _element_text(self, element):
        """Concatenate all descendant text of an element into one clean line."""
        text = "".join(element.itertext())
        return re.sub(r"\s+", " ", text).strip()

    # -- cover image -------------------------------------------------------

    def extract_cover_image(self, root):
        """Extract the cover image from a parsed FB2 tree.

        FB2 embeds images as base64 in <binary> elements; the cover is referenced
        from <description><title-info><coverpage><image href="#id"/>.

        Returns (image_bytes, extension) or (None, None).
        """
        ext_map = {
            "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png",
            "image/gif": ".gif", "image/webp": ".webp", "image/svg+xml": ".svg",
        }

        try:
            cover_id = None
            coverpage = root.find(".//description/title-info/coverpage")
            if coverpage is not None:
                image = coverpage.find("image")
                if image is not None:
                    # The href attribute lives in the xlink namespace; match by
                    # local name to stay namespace-agnostic.
                    href = self._href(image)
                    if href:
                        cover_id = href.lstrip("#")

            binaries = root.findall("binary")
            if not binaries:
                self.logger.info("No cover image found in FB2")
                return None, None

            target = None
            if cover_id:
                for b in binaries:
                    if b.get("id") == cover_id:
                        target = b
                        break
            # Fall back to the first image binary if the reference is missing.
            if target is None:
                for b in binaries:
                    if (b.get("content-type") or "").startswith("image/"):
                        target = b
                        break

            if target is None or not (target.text and target.text.strip()):
                self.logger.info("No cover image found in FB2")
                return None, None

            mime = (target.get("content-type") or "image/jpeg").strip().lower()
            ext = ext_map.get(mime, ".jpg")
            image_bytes = base64.b64decode(target.text.strip())
            self.logger.info(f"Found FB2 cover binary id='{target.get('id')}'")
            return image_bytes, ext
        except Exception as e:
            self.logger.error(f"Error extracting FB2 cover image: {e}")
            return None, None

    def _href(self, element):
        """Return an element's href attribute value, ignoring namespace."""
        for key, value in element.attrib.items():
            if etree.QName(key).localname == "href":
                return value
        return None

    def save_cover_image(self, image_bytes, extension, book_id):
        """Save extracted cover image to covers/ directory.

        Identical contract to EPUBProcessor.save_cover_image — returns a
        'covers/<id><ext>' relative path.
        """
        covers_dir = os.path.join(self.config.script_dir, "covers")
        os.makedirs(covers_dir, exist_ok=True)
        filename = f"{book_id}{extension}"
        filepath = os.path.join(covers_dir, filename)
        with open(filepath, "wb") as f:
            f.write(image_bytes)
        self.logger.info(f"Saved cover image to {filepath}")
        rel_path = f"covers/{filename}"
        try:
            import spaces
            if spaces.is_enabled(self.config):
                spaces.upload_relpath(self.config, rel_path)
        except Exception:
            pass
        return rel_path

    # -- queue / orchestration --------------------------------------------

    def add_chapters_to_queue(self, chapters, book_id=None, fb2_path=None):
        """Add extracted chapters to the translation queue."""
        if book_id is None:
            self.logger.error("book_id is required for adding chapters to queue")
            return 0

        added_count = 0
        for chapter in chapters:
            content = chapter["content"]
            content_lines = content.split("\n") if isinstance(content, str) else content

            queue_item_id = self.db_manager.add_to_queue(
                book_id=book_id,
                content=content_lines,
                title=chapter["title"],
                chapter_number=chapter["number"],
                source=chapter.get("file_path", fb2_path),
            )

            if queue_item_id:
                added_count += 1
                # Persist illustrations referenced by this chapter's content.
                if self._collector is not None:
                    try:
                        store_chapter_illustrations(
                            self.db_manager, self.config, book_id,
                            content_lines, self._collector, queue_id=queue_item_id,
                        )
                    except Exception as e:
                        self.logger.error(f"Failed to store illustrations for chapter {chapter['number']}: {e}")
            else:
                self.logger.error(f"Failed to add chapter {chapter['number']} to queue")

        self.logger.info(f"Added {added_count} chapters to queue")
        return added_count

    def process_fb2(self, fb2_path, book_id=None):
        """Process an FB2 file and add chapters to the queue.

        Returns (success, num_chapters, message).
        """
        root = self.load_fb2(fb2_path)
        if root is None:
            return False, 0, f"Failed to load FB2: {fb2_path}"

        # Enable illustration extraction for this run.
        self._collector = IllustrationCollector()

        chapters = self.extract_chapters(root)
        if not chapters:
            return False, 0, "No chapters found in FB2"

        num_added = self.add_chapters_to_queue(chapters, book_id, fb2_path)
        return True, num_added, f"Successfully added {num_added} chapters to queue"
