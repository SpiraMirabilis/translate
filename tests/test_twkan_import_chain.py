"""Pin the modules/twkan.py -> strip_queue_confusable_lines.py import chain.

strip_queue_confusable_lines.py is a root utility script, but unlike its
siblings it is part of the production import graph: modules/twkan.py reuses
its confusable-skeleton text functions. The refactor passes must not break
this (root stays importable; the four functions stay module-level).
"""


def test_twkan_module_imports():
    import modules.twkan  # noqa: F401


def test_script_functions_importable():
    from strip_queue_confusable_lines import (  # noqa: F401
        build_matcher,
        filter_lines,
        load_confusables,
        make_skeletonizer,
    )


def test_filter_lines_pure_behavior():
    from strip_queue_confusable_lines import filter_lines

    content = "keep this line\ntwkan.com spam here\nkeep this too"
    new_content, removed = filter_lines(content, lambda line: "twkan" in line)
    assert removed == ["twkan.com spam here"]
    assert "twkan" not in new_content
    assert "keep this line" in new_content and "keep this too" in new_content
