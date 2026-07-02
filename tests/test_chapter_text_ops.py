"""Tests for chapter_text_ops — pure text transforms extracted from the
web/api/entities.py handlers (B4).

Expected outputs were constructed against the ORIGINAL inline handler
behavior (decase_entity and propagate_change's substitute branch), so
these tests pin the extraction to exact pre-existing semantics.
"""
import sys
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from chapter_text_ops import (
    decase_lines,
    substitute_in_lines,
    build_case_preserving_replacer,
    source_mentions,
)


# ------------------------------------------------------------------
# decase_lines
# ------------------------------------------------------------------

class TestDecaseLines:
    def test_mid_sentence_occurrence_lowercased(self):
        lines = ["He greeted the Elder warmly"]
        new_lines, n = decase_lines(lines, "Elder")
        assert new_lines == ["He greeted the elder warmly"]
        assert n == 1

    def test_line_start_preserved(self):
        lines = ["Elder went home"]
        new_lines, n = decase_lines(lines, "Elder")
        assert new_lines == ["Elder went home"]
        assert n == 0

    def test_leading_whitespace_still_counts_as_line_start(self):
        lines = ["   Elder went home"]
        new_lines, n = decase_lines(lines, "Elder")
        assert new_lines == ["   Elder went home"]
        assert n == 0

    def test_sentence_start_after_period_preserved(self):
        lines = ["He left. Elder stayed behind"]
        new_lines, n = decase_lines(lines, "Elder")
        assert new_lines == ["He left. Elder stayed behind"]
        assert n == 0

    def test_sentence_start_after_exclamation_and_question(self):
        lines = ["Run! Elder is coming", "Really? Elder said so"]
        new_lines, n = decase_lines(lines, "Elder")
        assert new_lines == ["Run! Elder is coming", "Really? Elder said so"]
        assert n == 0

    def test_sentence_start_with_multiple_spaces_preserved(self):
        lines = ["He left.  Elder stayed"]
        new_lines, n = decase_lines(lines, "Elder")
        assert new_lines == ["He left.  Elder stayed"]
        assert n == 0

    def test_quote_prefixes_preserved(self):
        for q in ['"', '“', '‘', "'", '【']:
            lines = [f'He said {q}Elder come here']
            new_lines, n = decase_lines(lines, "Elder")
            assert new_lines == lines, f"quote prefix {q!r} should preserve caps"
            assert n == 0

    def test_after_comma_is_lowercased(self):
        lines = ["Well, Elder said so"]
        new_lines, n = decase_lines(lines, "Elder")
        assert new_lines == ["Well, elder said so"]
        assert n == 1

    def test_protected_compound_term_preserved(self):
        # "Elder Hall" is another entity's translation containing "Elder";
        # the standalone occurrence is decased, the compound one is not.
        lines = ["They met Elder Hall and the Elder together"]
        new_lines, n = decase_lines(
            lines, "Elder", protected_terms={"Elder Hall"}
        )
        assert new_lines == ["They met Elder Hall and the elder together"]
        assert n == 1

    def test_word_boundary_no_match_inside_longer_word(self):
        lines = ["The Elderly man waved"]
        new_lines, n = decase_lines(lines, "Elder")
        assert new_lines == ["The Elderly man waved"]
        assert n == 0

    def test_non_alpha_final_char_matches_without_boundary(self):
        # word ending in a non-letter: pattern has no \b, matches literally
        lines = ["He used Fist-1s today"]
        new_lines, n = decase_lines(lines, "Fist-1")
        assert new_lines == ["He used fist-1s today"]
        assert n == 1

    def test_only_first_char_lowered_in_multiword_term(self):
        # Parity quirk: lowered = word[0].lower() + word[1:] — later words
        # keep their capitals.
        lines = ["He drew the Azure Sword quickly"]
        new_lines, n = decase_lines(lines, "Azure Sword")
        assert new_lines == ["He drew the azure Sword quickly"]
        assert n == 1

    def test_count_is_per_changed_line_not_per_match(self):
        lines = ["the Elder met the Elder", "the Elder nodded"]
        new_lines, n = decase_lines(lines, "Elder")
        assert new_lines == ["the elder met the elder", "the elder nodded"]
        assert n == 2  # two changed lines, three substitutions

    def test_explicit_lowered_argument_used(self):
        lines = ["the Elder nodded"]
        new_lines, n = decase_lines(lines, "Elder", lowered="eLDER")
        assert new_lines == ["the eLDER nodded"]
        assert n == 1


# ------------------------------------------------------------------
# substitute_in_lines / build_case_preserving_replacer
# ------------------------------------------------------------------

class TestSubstituteInLines:
    def test_simple_substitution(self):
        lines = ["He raised the azure sword high"]
        new_lines, n = substitute_in_lines(lines, "azure sword", "crimson blade")
        assert new_lines == ["He raised the crimson blade high"]
        assert n == 1

    def test_sentence_start_capital_carried_to_new_words(self):
        lines = ["Azure sword flashed"]
        new_lines, n = substitute_in_lines(lines, "azure sword", "crimson blade")
        assert new_lines == ["Crimson blade flashed"]
        assert n == 1

    def test_all_caps_carried(self):
        lines = ["CHAPTER: AZURE SWORD RETURNS"]
        new_lines, n = substitute_in_lines(lines, "azure sword", "crimson blade")
        assert new_lines == ["CHAPTER: CRIMSON BLADE RETURNS"]
        assert n == 1

    def test_pure_case_correction_applied_as_written(self):
        # The old translation's own casing is NOT preserved — a case-only
        # correction must actually apply.
        lines = ["the azure sword hummed"]
        new_lines, n = substitute_in_lines(lines, "azure sword", "Azure Sword")
        assert new_lines == ["the Azure Sword hummed"]
        assert n == 1

    def test_lowercase_shift_carried(self):
        # Old canonical form is capitalized; chapter has it lowercased →
        # the same lowercase shift is applied to the new word.
        lines = ["a mere sword cultivator"]
        new_lines, n = substitute_in_lines(lines, "Sword", "Blade")
        assert new_lines == ["a mere blade cultivator"]
        assert n == 1

    def test_new_translation_with_more_words(self):
        # Extra new words have no positional reference → used verbatim.
        lines = ["the sword gleamed"]
        new_lines, n = substitute_in_lines(lines, "Sword", "Blade of Dawn")
        assert new_lines == ["the blade of Dawn gleamed"]
        assert n == 1

    def test_new_translation_with_fewer_words(self):
        lines = ["the azure sword gleamed"]
        new_lines, n = substitute_in_lines(lines, "azure sword", "saber")
        assert new_lines == ["the saber gleamed"]
        assert n == 1

    def test_internal_caps_preserved_verbatim(self):
        lines = ["she opened heavennet today"]
        new_lines, n = substitute_in_lines(lines, "heavennet", "HeavenNet")
        assert new_lines == ["she opened HeavenNet today"]
        assert n == 1

    def test_count_is_changed_lines(self):
        lines = ["sword and sword", "no match here", "a sword"]
        new_lines, n = substitute_in_lines(lines, "sword", "blade")
        assert new_lines == ["blade and blade", "no match here", "a blade"]
        assert n == 2

    def test_unchanged_lines_returned_intact(self):
        lines = ["nothing to do"]
        new_lines, n = substitute_in_lines(lines, "sword", "blade")
        assert new_lines == lines
        assert n == 0

    def test_replacer_match_case_direct(self):
        import re
        replacer = build_case_preserving_replacer("azure sword", "crimson blade")
        m = re.match(r".*", "Azure Sword")
        assert replacer(m) == "Crimson Blade"


# ------------------------------------------------------------------
# source_mentions
# ------------------------------------------------------------------

class TestSourceMentions:
    def test_json_encoded_source_contains_cjk_literal(self):
        import json
        raw = json.dumps(["他看着张羽离开", "第二段"], ensure_ascii=False)
        assert source_mentions(raw, "张羽")
        assert not source_mentions(raw, "李四")

    def test_plain_text_legacy_source(self):
        assert source_mentions("他看着张羽离开", "张羽")

    def test_empty_or_none_source(self):
        assert not source_mentions("", "张羽")
        assert not source_mentions(None, "张羽")


class TestBuildSubstitutionPattern:
    """Word-boundary fencing (used by correct_entity_translation.py -w)."""

    def test_plain_pattern_matches_inside_words(self):
        from chapter_text_ops import build_substitution_pattern
        p = build_substitution_pattern("Dai")
        assert p.search("Daiyu smiled")  # substring hit without fencing

    def test_word_boundary_skips_embedded(self):
        from chapter_text_ops import build_substitution_pattern
        p = build_substitution_pattern("Dai", word_boundary=True)
        assert p.search("Dai nodded")
        assert p.search("with Dai.")
        assert not p.search("Daiyu smiled")
        assert not p.search("in Dailan city")

    def test_word_boundary_with_nonword_edges(self):
        # Lookarounds (not \b) so fencing works when the term starts/ends
        # with a non-word character.
        from chapter_text_ops import build_substitution_pattern
        p = build_substitution_pattern("'Azure'", word_boundary=True)
        assert p.search("the 'Azure' blade")

    def test_case_insensitive(self):
        from chapter_text_ops import build_substitution_pattern
        p = build_substitution_pattern("Azure Sword", word_boundary=True)
        assert p.search("the AZURE SWORD gleamed")

    def test_substitute_in_lines_word_boundary_passthrough(self):
        from chapter_text_ops import substitute_in_lines
        lines = ["Dai nodded.", "Daiyu smiled."]
        new_lines, count = substitute_in_lines(lines, "Dai", "Tai", word_boundary=True)
        assert new_lines == ["Tai nodded.", "Daiyu smiled."]
        assert count == 1
