"""Footnotes in EPUB output.

Chapters store footnotes as a rendered convention (footnotes.py): an inline
"[n]" marker plus a trailing "[n] body" definition block. The EPUB exporter
lifts that into EPUB3 note semantics — epub:type="noteref" links and
epub:type="footnote" <aside>s — which readers render as popups.
"""
import logging
import os
import zipfile

import pytest

from config import TranslationConfig
from output_formatter import (
    OutputFormatter,
    _footnotes_section_html,
    _linkify_footnotes,
    _mark_footnote_refs,
)


class TestMarkFootnoteRefs:
    def test_marks_marker_that_has_a_definition(self):
        lines, used = _mark_footnote_refs(["The Calabash Brothers[1] arrived."], {"1"})
        assert lines == ["The Calabash Brothers⟦FN:1⟧ arrived."]
        assert used == {"1"}

    def test_marker_without_a_definition_stays_prose(self):
        # Danmaku counts and citations look like markers but aren't ones.
        lines, used = _mark_footnote_refs(["A count flew past: [666] of them."], {"1"})
        assert lines == ["A count flew past: [666] of them."]
        assert used == set()

    def test_marker_inside_a_code_span_stays_literal(self):
        lines, used = _mark_footnote_refs(["He read `arr[1]` aloud."], {"1"})
        assert lines == ["He read `arr[1]` aloud."]
        assert used == set()

    def test_link_text_that_is_a_bare_number_is_not_a_marker(self):
        lines, used = _mark_footnote_refs(["See [1](https://example.com) now."], {"1"})
        assert lines == ["See [1](https://example.com) now."]
        assert used == set()

    def test_no_definitions_means_no_marking(self):
        lines, used = _mark_footnote_refs(["Nothing[1] here."], set())
        assert lines == ["Nothing[1] here."]
        assert used == set()

    def test_repeated_marker_reports_the_id_once(self):
        lines, used = _mark_footnote_refs(["One[1] and[2] and[1]."], {"1", "2"})
        assert lines == ["One⟦FN:1⟧ and⟦FN:2⟧ and⟦FN:1⟧."]
        assert used == {"1", "2"}


class TestLinkifyFootnotes:
    def test_sentinel_becomes_a_noteref_link(self):
        html = _linkify_footnotes("<p>Brothers⟦FN:3⟧ arrived.</p>")
        assert 'epub:type="noteref"' in html
        assert 'href="#fn3"' in html
        assert 'id="fnref3"' in html
        assert ">[3]</a>" in html

    def test_html_without_sentinels_is_returned_untouched(self):
        assert _linkify_footnotes("<p>Plain.</p>") == "<p>Plain.</p>"


class TestFootnotesSection:
    def test_referenced_note_becomes_an_aside_with_a_backlink(self):
        html = _footnotes_section_html({1: "Calabash Brothers: a 1980s animation."}, {"1"})
        assert 'epub:type="footnotes"' in html
        assert '<aside class="footnote" epub:type="footnote" id="fn1">' in html
        assert 'href="#fnref1"' in html
        assert "a 1980s animation." in html

    def test_unreferenced_note_keeps_its_text_but_emits_no_dangling_backlink(self):
        # An aside nobody links to is hidden from the flow by popup-capable
        # readers, and a backlink to an fnref that was never emitted is a
        # dangling fragment. So it degrades to a plain paragraph.
        html = _footnotes_section_html({1: "Orphaned note."}, set())
        assert "Orphaned note." in html
        assert "<aside" not in html
        assert "#fnref1" not in html

    def test_no_definitions_emits_nothing(self):
        assert _footnotes_section_html({}, set()) == ""

    def test_body_opening_with_a_block_does_not_nest_it_inside_a_paragraph(self):
        # Prefixing the marker inline would produce <p><blockquote>… — invalid XHTML.
        html = _footnotes_section_html({1: "> quoted body"}, {"1"})
        assert "<p><blockquote>" not in html
        assert "<blockquote>" in html


class TestEpubIntegration:
    @pytest.fixture
    def formatter(self, tmp_path):
        config = TranslationConfig()
        config.db_backend = "sqlite"
        config.output_dir = str(tmp_path)
        return OutputFormatter(config, logging.getLogger("test"))

    def _chapter_html(self, formatter, content, tmp_path):
        path = formatter.save_book_as_epub(
            [{"chapter": 1, "title": "T", "content": content}],
            {"title": "FN", "author": "A", "language": "en", "id": 1},
            output_path=os.path.join(str(tmp_path), "fn.epub"),
        )
        assert path, "EPUB build failed"
        with zipfile.ZipFile(path) as z:
            return z.read("EPUB/chapter_001.xhtml").decode()

    def test_chapter_gets_noteref_and_aside(self, formatter, tmp_path):
        html = self._chapter_html(formatter, [
            "The Calabash Brothers[1] arrived.",
            "",
            "[1] Calabash Brothers: a 1980s animation.",
        ], tmp_path)
        assert 'epub:type="noteref"' in html
        assert 'epub:type="footnote"' in html
        # The epub prefix must be declared or the XHTML is not well-formed.
        assert 'xmlns:epub="http://www.idpf.org/2007/ops"' in html

    def test_definition_block_leaves_the_prose_flow(self, formatter, tmp_path):
        html = self._chapter_html(formatter, [
            "Brothers[1] arrived.",
            "",
            "[1] A 1980s animation.",
        ], tmp_path)
        body = html.split("<section")[0]
        assert "A 1980s animation." not in body  # only in the notes section
        assert html.count("A 1980s animation.") == 1

    def test_chapter_without_footnotes_gets_no_notes_section(self, formatter, tmp_path):
        html = self._chapter_html(formatter, ["Just prose.", "", "More prose."], tmp_path)
        assert "footnotes" not in html
        assert "<aside" not in html

    def test_every_noteref_resolves_to_an_aside(self, formatter, tmp_path):
        import re
        html = self._chapter_html(formatter, [
            "One[1] two[2] three[3].",
            "",
            "[1] First.",
            "[2] Second.",
            "[3] Third.",
        ], tmp_path)
        targets = set(re.findall(r'href="#(fn\d+)"', html))
        ids = set(re.findall(r'id="(fn\d+)"', html))
        assert targets and targets == ids  # no dangling fragments
