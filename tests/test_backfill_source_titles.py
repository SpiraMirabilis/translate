"""backfill_source_titles.py — the offline parts: page extract, title cleanup,
t2s. The scraper and Haiku calls are not exercised here."""
import backfill_source_titles as bst

PAGE = """<html><head><title>我在仙界有個爹最新章節_台灣小說網</title>
<meta property="og:novel:book_name" content="我在仙界有個爹">
<meta property="og:title" content="我在仙界有個爹">
<meta name="keywords" content="我在仙界有個爹,仙俠">
<meta name="viewport" content="width=device-width">
<script>var junk = 1;</script></head>
<body><h1>我在仙界有個爹</h1><h2>最新章節</h2><h2>最新章節</h2></body></html>"""


def test_page_extract_keeps_only_naming_parts():
    ex = bst.page_extract(PAGE)
    assert ex["meta"]["og:novel:book_name"] == "我在仙界有個爹"
    assert "viewport" not in ex["meta"]
    assert ex["headings"] == ["我在仙界有個爹", "最新章節"]  # deduped
    assert "junk" not in str(ex)


def test_clean_title_strips_brackets_and_blanks():
    assert bst.clean_title(" 《新白蛇问仙》 ") == "新白蛇问仙"
    assert bst.clean_title("") is None
    assert bst.clean_title(None) is None


def test_to_simplified_only_for_chinese_sources():
    assert bst.to_simplified({"source_language": "zh"}, "我在仙界有個爹") == "我在仙界有个爹"
    assert bst.to_simplified({"source_language": "zh"}, "新白蛇问仙") == "新白蛇问仙"
    # Japanese/Korean titles keep their own glyphs
    assert bst.to_simplified({"source_language": "ja"}, "魔術學院") == "魔術學院"
