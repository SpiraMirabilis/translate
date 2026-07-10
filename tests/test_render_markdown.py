"""Tests for output_formatter._render_markdown — pipe-table parity fixups and
the sentinel ⟦TABLE⟧ rich-table format (mirrors chapterMarkdown.js) — plus
render_lines_html, the shared line-array renderer used by WordPress
publishing and the book HTML export."""

from output_formatter import _render_markdown, _parse_table_run, render_lines_html
from web.services.wp_client import content_to_html

TABLE_LINES = [
    '⟦TABLE⟧',
    '⟦TR⟧',
    '⟦TH:center⟧', 'Stat', '⟦/TH⟧',
    '⟦TH⟧', 'Value', '⟦/TH⟧',
    '⟦/TR⟧',
    '⟦TR⟧',
    '⟦TD⟧', 'HP: 100', 'MP: 50', '', '- Fireball', '- Ice Lance', '⟦/TD⟧',
    '⟦TD⟧', '⟦/TD⟧',
    '⟦/TR⟧',
    '⟦/TABLE⟧',
]


class TestParseTableRun:
    def test_parses_rich_table(self):
        parsed = _parse_table_run(TABLE_LINES, 0)
        assert parsed is not None
        table, nxt = parsed
        assert nxt == len(TABLE_LINES)
        assert table['rows'][0]['cells'][0] == {
            'header': True, 'align': 'center', 'lines': ['Stat']}
        assert table['rows'][1]['cells'][0]['lines'] == [
            'HP: 100', 'MP: 50', '', '- Fireball', '- Ice Lance']
        assert table['rows'][1]['cells'][1]['lines'] == []

    def test_rejects_malformed(self):
        cases = [
            ['⟦TABLE⟧', '⟦TR⟧', '⟦TD⟧', 'x', '⟦/TD⟧', '⟦/TR⟧'],  # EOF
            ['⟦TABLE⟧', '⟦TR⟧', '⟦TH⟧', 'x', '⟦/TD⟧', '⟦/TR⟧', '⟦/TABLE⟧'],
            ['⟦TABLE⟧', '⟦/TABLE⟧'],
            ['⟦TABLE⟧', '⟦TR⟧', '⟦/TR⟧', '⟦/TABLE⟧'],
            ['⟦TABLE⟧', 'prose', '⟦/TABLE⟧'],
            ['⟦TABLE⟧', '⟦TR⟧', '⟦TD⟧', '⟦TABLE⟧', '⟦/TD⟧', '⟦/TR⟧', '⟦/TABLE⟧'],
            ['⟦TABLE⟧', '⟦TR⟧', '⟦TD⟧', '⟦IMG:abcd⟧', '⟦/TD⟧', '⟦/TR⟧', '⟦/TABLE⟧'],
            ['⟦TABLE⟧', '⟦TR⟧', '⟦TD⟧', 'x', '⟦/TD⟧', '⟦/TABLE⟧'],
        ]
        for c in cases:
            assert _parse_table_run(c, 0) is None, c

    def test_whitespace_tolerant(self):
        parsed = _parse_table_run(
            ['  ⟦TABLE⟧ ', ' ⟦TR⟧', '⟦TD⟧ ', 'x', ' ⟦/TD⟧', '⟦/TR⟧ ', ' ⟦/TABLE⟧'], 0)
        assert parsed is not None


class TestRenderMarkdownTables:
    def test_sentinel_table_renders_structure(self):
        html = _render_markdown('\n'.join(TABLE_LINES))
        assert '<table>' in html
        assert '<thead>' in html
        assert '<th align="center">' in html
        assert '<tbody>' in html
        assert '<li>Fireball</li>' in html          # list inside a cell
        assert '<br' in html                        # HP/MP hard break

    def test_header_only_table_omits_tbody(self):
        html = _render_markdown('\n'.join(
            ['⟦TABLE⟧', '⟦TR⟧', '⟦TH⟧', 'only', '⟦/TH⟧', '⟦/TR⟧', '⟦/TABLE⟧']))
        assert '<thead>' in html
        assert '<tbody>' not in html

    def test_table_between_prose(self):
        text = '\n'.join(['before', '', *TABLE_LINES, '', 'after'])
        html = _render_markdown(text)
        assert '<p>before</p>' in html
        assert '<p>after</p>' in html
        assert html.index('before') < html.index('<table>') < html.index('after')

    def test_malformed_run_falls_through_as_prose(self):
        html = _render_markdown('\n'.join(['⟦TABLE⟧', '⟦TR⟧', 'broken']))
        assert '<table>' not in html
        assert '⟦TABLE⟧' in html  # markers visible, data preserved

    def test_xss_in_cell_is_sanitized(self):
        html = _render_markdown('\n'.join(
            ['⟦TABLE⟧', '⟦TR⟧', '⟦TD⟧', '<script>alert(1)</script>', '⟦/TD⟧',
             '⟦/TR⟧', '⟦/TABLE⟧']))
        assert '<script' not in html

    def test_pipe_table_parity_preserved(self):
        # header-only pipe table: spurious empty body row stripped (existing fix)
        html = _render_markdown('| X |\n| --- |')
        assert '<table>' in html
        assert '<tbody>' not in html
        assert '<td></td>' not in html

    def test_plain_prose_unaffected(self):
        html = _render_markdown('line1\nline2\n\n- item')
        assert '<br' in html
        assert '<li>item</li>' in html


class TestInlineSentinels:
    def test_underline_and_color(self):
        html = _render_markdown('a ⟦U⟧under⟦/U⟧ and ⟦COLOR:#ff0000⟧red⟦/COLOR⟧ b')
        assert '<u>under</u>' in html
        assert '<span style="color:#ff0000">red</span>' in html

    def test_nested_with_markdown_marks(self):
        html = _render_markdown('⟦U⟧**bold**⟦/U⟧')
        assert '<u><strong>bold</strong></u>' in html

    def test_in_table_cells(self):
        html = _render_markdown('\n'.join(
            ['⟦TABLE⟧', '⟦TR⟧', '⟦TD⟧', '⟦COLOR:#00ff00⟧hp⟦/COLOR⟧', '⟦/TD⟧',
             '⟦/TR⟧', '⟦/TABLE⟧']))
        assert '<span style="color:#00ff00">hp</span>' in html

    def test_invalid_and_unmatched_stay_literal(self):
        assert '⟦U⟧' in _render_markdown('lonely ⟦U⟧ marker')
        assert '⟦/COLOR⟧' in _render_markdown('bad ⟦/COLOR⟧ close')
        assert '⟦COLOR:red⟧' in _render_markdown('⟦COLOR:red⟧named⟦/COLOR⟧')
        misnested = _render_markdown('⟦U⟧a⟦COLOR:#112233⟧b⟦/U⟧c⟦/COLOR⟧')
        assert '<u>' not in misnested
        assert '<span style="color:#112233">' in misnested

    def test_code_spans_stay_literal_but_pairs_may_span_code(self):
        in_code = _render_markdown('`⟦U⟧ x ⟦/U⟧`')
        assert '<u>' not in in_code
        around = _render_markdown('⟦U⟧a `code` b⟦/U⟧')
        assert '<u>a <code>code</code> b</u>' in around

    def test_never_pairs_across_paragraphs(self):
        html = _render_markdown('⟦U⟧first\n\nsecond⟦/U⟧')
        assert '<u>' not in html


class TestRenderLinesHtml:
    def test_paragraphs_tables_and_img_placeholders(self):
        lines = ['Intro line.', '', '⟦IMG:abcd⟧', *TABLE_LINES, '', 'Outro.']
        html = render_lines_html(lines)
        assert '<p>Intro line.</p>' in html
        assert '<p>⟦IMG:abcd⟧</p>' in html          # placeholder kept literal
        assert '<table>' in html
        assert '<li>Fireball</li>' in html
        assert '<p>Outro.</p>' in html

    def test_empty_and_none_lines(self):
        assert render_lines_html([]) == ''
        assert render_lines_html(None) == ''

    def test_wordpress_content_to_html_uses_shared_renderer(self):
        lines = ['| a | b |', '| --- | --- |', '| c | d |']
        html = content_to_html(lines)
        assert '<table>' in html                     # was literal pipes before
        assert content_to_html(TABLE_LINES).count('<td') == 2


class TestReaderParityRegressions:
    """Constructs the write editor emits that must render the same in the
    Python pipeline (EPUB/WordPress/HTML export) as in the Reader
    (markdown-it): strikethrough and 4-space nested lists."""

    def test_strikethrough_renders_as_s_tag(self):
        html = _render_markdown('before ~~gone~~ after')
        assert '<s>gone</s>' in html
        assert '~~' not in html

    def test_strikethrough_stays_literal_in_code_spans(self):
        html = _render_markdown('`~~kept~~` and ~~struck~~')
        assert '~~kept~~' in html
        assert '<s>struck</s>' in html

    def test_strikethrough_inside_sentinel_table_cells(self):
        html = render_lines_html([
            '⟦TABLE⟧', '⟦TR⟧', '⟦TD⟧', 'cell ~~x~~', '⟦/TD⟧', '⟦/TR⟧', '⟦/TABLE⟧',
        ])
        assert '<s>x</s>' in html

    def test_four_space_nested_lists_nest(self):
        # The write editor serializes nested lists with 4-space child indents
        # (writeMarkdown.js serializeList) precisely so this nests here.
        html = _render_markdown('- outer\n    - inner one\n    - inner two\n- outer two')
        assert html.count('<ul>') == 2

    def test_nested_list_under_ordered_item(self):
        html = _render_markdown('1. first\n    - sub\n2. second')
        assert '<ol>' in html and '<ul>' in html
