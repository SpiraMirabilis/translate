"""The JSON response contract, owned by code rather than by prompt text.

Every book froze a copy of a genre prompt into ``books.prompt_template`` at
creation time, so anything stated only in the prompt corpus reaches books
created afterwards and no others. The response contract — the shape of the JSON
the model must return — is precisely the thing that must NOT drift that way:
when ``note_updates`` was added, 44 of the 67 frozen templates never learned
about it, and the channel worked there only because ``generate_system_prompt``
separately injects an ENTITY NOTES section from code that restates it.

So the contract lives here, is stripped out of whatever the book's prompt still
says about it, and is appended from code on every run. What stays editable in
the prompt corpus is what should be editable: genre and style guidance, what
counts as an entity, and BOOK-SPECIFIC NOTES.

Two halves:
  * ``response_contract_section`` renders the RESPONSE FORMAT block that
    ``translation_engine.generate_system_prompt`` appends.
  * ``strip_legacy_contract`` removes the pre-extraction wording from a stored
    prompt. It runs at assembly time on every prompt (idempotent, and the only
    thing keeping an un-backfilled book coherent) and is the engine of
    ``backfill_prompt_contract.py``, which writes the cleaned form back.
"""

import re

# ── example prose for the response template ──────────────────────────────────
#
# The template's title/summary/content examples set the register the model
# answers in, and its entity examples are what a book with an empty glossary
# shows the model — chapter 1 of a new book would otherwise be handed
# "示例characters": "Example Character". Both are worth keeping genre-specific.
# Keyed by ``books.source_language``: there is no genre column on books
# (genres.json is consulted once, at creation, to pick a prompt file), and
# language maps 1:1 onto the four prompt files these were lifted from.

GENRE_EXAMPLES = {
    "zh": {
        "title": "Chapter 3 - The Great Apocalyptic Battle",
        "summary": "A concise summary of no more than 75 words.",
        "content": [
            "The warriors gathered at the base of the mountain, their weapons gleaming under the pale moonlight.",
            "",
            "\"We have no choice,\" Lin Feng said, gripping the hilt of his sword. \"If we don't act now, the Scarlet Flame Sect will destroy everything.\"",
            "",
            "A cold wind swept across the battlefield as the first clash of steel echoed through the valley.",
        ],
        "entities": {
                "characters": {
                        "钟岳": {
                                "translation": "Zhong Yue",
                                "gender": "male"
                        },
                        "夏儿": {
                                "translation": "Xia'er",
                                "gender": "female"
                        },
                        "方剑": {
                                "translation": "Fang Jian",
                                "gender": "male"
                        }
                },
                "places": {
                        "剑门山": {
                                "translation": "Jianmen Mountain"
                        },
                        "大荒": {
                                "translation": "Great Wilderness"
                        },
                        "染霜城": {
                                "translation": "Frostveil City"
                        }
                },
                "organizations": {
                        "风氏": {
                                "translation": "Feng Clan"
                        }
                },
                "abilities": {
                        "太极拳": {
                                "translation": "Supreme Ultimate Fist"
                        },
                        "天级上品武技·星陨斩": {
                                "translation": "Heaven Rank Martial Skill: Starfall Slash"
                        }
                },
                "titles": {
                        "鉴宝师": {
                                "translation": "Treasure Appraiser"
                        },
                        "真君": {
                                "translation": "True Sovereign"
                        }
                },
                "equipment": {
                        "蓝龙药鼎": {
                                "translation": "Azure Dragon Medicinal Cauldron"
                        },
                        "血魔九影剑": {
                                "translation": "Blood Demon Nine Shadows Sword"
                        }
                },
                "creatures": {
                        "渊狼": {
                                "translation": "Abyssal Wolf"
                        }
                },
                "cultivation terms": {
                        "筑基": {
                                "translation": "Foundation Establishment"
                        }
                }
        },
    },
    "ja": {
        "title": "Chapter 3 - The Demon King's Invitation",
        "summary": "A concise summary of no more than 75 words.",
        "content": [
            "The guild hall was bustling with adventurers when Takahashi Ren pushed open the heavy wooden doors.",
            "",
            "\"Ren-kun, over here!\" Sakura-san called out, waving from a corner table. \"We've got a new quest from the Adventurer's Guild.\"",
            "",
            "He slid into the seat across from her, his hand instinctively resting on the hilt of the Holy Blade Excalibur at his waist.",
        ],
        "entities": {
                "characters": {
                        "高橋蓮": {
                                "translation": "Takahashi Ren",
                                "gender": "male"
                        },
                        "桜": {
                                "translation": "Sakura",
                                "gender": "female"
                        },
                        "魔王ゼノス": {
                                "translation": "Demon King Xenos",
                                "gender": "male"
                        }
                },
                "places": {
                        "東京タワー": {
                                "translation": "Tokyo Tower"
                        },
                        "黒の森": {
                                "translation": "Black Forest"
                        },
                        "魔王城": {
                                "translation": "Demon King's Castle"
                        }
                },
                "organizations": {
                        "冒険者ギルド": {
                                "translation": "Adventurer's Guild"
                        }
                },
                "abilities": {
                        "聖剣術": {
                                "translation": "Holy Sword Art"
                        },
                        "炎魔法・紅蓮": {
                                "translation": "Fire Magic: Crimson Lotus"
                        }
                },
                "titles": {
                        "勇者": {
                                "translation": "Hero"
                        },
                        "Sランク": {
                                "translation": "S-Rank"
                        }
                },
                "items": {
                        "聖剣エクスカリバー": {
                                "translation": "Holy Blade Excalibur"
                        },
                        "回復ポーション": {
                                "translation": "Recovery Potion"
                        }
                }
        },
    },
    "ko": {
        "title": "Chapter 3 - The Gate Opens",
        "summary": "A concise summary of no more than 75 words.",
        "content": [
            "The dungeon gate shimmered with an ominous crimson light as Kim Jinwoo approached.",
            "",
            "\"Team leader, the mana reading is off the charts,\" Park Soyeon said, checking her scanner. \"This is at least an A-Rank gate.\"",
            "",
            "Jinwoo drew the Shadowfang Blade from its scabbard and stepped forward without hesitation.",
        ],
        "entities": {
                "characters": {
                        "김진우": {
                                "translation": "Kim Jinwoo",
                                "gender": "male"
                        },
                        "박소연": {
                                "translation": "Park Soyeon",
                                "gender": "female"
                        },
                        "마왕 제노스": {
                                "translation": "Demon King Xenos",
                                "gender": "male"
                        }
                },
                "places": {
                        "서울": {
                                "translation": "Seoul"
                        },
                        "마왕의 성": {
                                "translation": "Demon King's Fortress"
                        },
                        "붉은 사막": {
                                "translation": "Red Desert"
                        }
                },
                "organizations": {
                        "헌터 협회": {
                                "translation": "Hunter Association"
                        },
                        "적월 길드": {
                                "translation": "Red Moon Guild"
                        }
                },
                "abilities": {
                        "그림자 이동": {
                                "translation": "Shadow Step"
                        },
                        "용의 숨결": {
                                "translation": "Dragon's Breath"
                        }
                },
                "titles": {
                        "S급 헌터": {
                                "translation": "S-Rank Hunter"
                        },
                        "검성": {
                                "translation": "Sword Saint"
                        }
                },
                "equipment": {
                        "그림자 송곳니": {
                                "translation": "Shadowfang Blade"
                        },
                        "불사의 반지": {
                                "translation": "Ring of Immortality"
                        }
                },
                "creatures": {
                        "레드 드래곤": {
                                "translation": "Red Dragon"
                        }
                }
        },
    },
    "ru": {
        "title": "Chapter 3 - The Gate Opens",
        "summary": "A concise summary of no more than 75 words.",
        "content": [
            "The dungeon gate shimmered with an ominous crimson light as Alexey approached.",
            "",
            "\"Captain, the mana reading is off the charts,\" Ekaterina said, checking her scanner. \"This is at least an A-Rank gate.\"",
            "",
            "Alexey drew the Shadowfang Blade from its scabbard and stepped forward without hesitation.",
        ],
        "entities": {
                "characters": {
                        "Алексей": {
                                "translation": "Alexey",
                                "gender": "male"
                        },
                        "Екатерина": {
                                "translation": "Ekaterina",
                                "gender": "female"
                        },
                        "князь Олег": {
                                "translation": "Prince Oleg",
                                "gender": "male"
                        }
                },
                "places": {
                        "Москва": {
                                "translation": "Moscow"
                        },
                        "Багровая Пустошь": {
                                "translation": "Crimson Wastes"
                        },
                        "Красная Пустыня": {
                                "translation": "Red Desert"
                        }
                },
                "organizations": {
                        "Гильдия Охотников": {
                                "translation": "Hunter Guild"
                        },
                        "Гильдия Алого Месяца": {
                                "translation": "Red Moon Guild"
                        }
                },
                "abilities": {
                        "Теневой Шаг": {
                                "translation": "Shadow Step"
                        },
                        "Дыхание Дракона": {
                                "translation": "Dragon's Breath"
                        }
                },
                "titles": {
                        "Охотник ранга S": {
                                "translation": "S-Rank Hunter"
                        },
                        "Мечник": {
                                "translation": "Sword Saint"
                        }
                },
                "equipment": {
                        "Теневой Клык": {
                                "translation": "Shadowfang Blade"
                        },
                        "Кольцо Бессмертия": {
                                "translation": "Ring of Immortality"
                        }
                },
                "creatures": {
                        "Красный Дракон": {
                                "translation": "Red Dragon"
                        }
                }
        },
    },
}

DEFAULT_EXAMPLE_LANG = "zh"

TEMPLATE_START = "++++ Response Template Example"
TEMPLATE_END = "++++ Response Template End"


def genre_example(source_language=None):
    """Example title/summary/content for a book's source language."""
    key = (source_language or "").strip().lower()[:2]
    return GENRE_EXAMPLES.get(key) or GENRE_EXAMPLES[DEFAULT_EXAMPLE_LANG]


# ── the contract section ─────────────────────────────────────────────────────

_PROSE_RULES = (
    "Output must be valid JSON matching the template below exactly. Return the "
    "JSON object and nothing else — no prose, no code fences, no commentary."
)

_ENTITY_KEY_RULE = (
    "Every entity key MUST be the original untranslated text exactly as it "
    "appears in the source. Never use an English key."
)

_EXACT_SIMILAR = (
    "The PRE-TRANSLATED ENTITIES block above is split per category into two "
    "sub-objects:\n"
    "- \"exact\": entities whose source-language form appears literally in this "
    "chapter's source text. Use these translations exactly when they appear.\n"
    "- \"similar\": entities NOT necessarily in this chapter, but that share "
    "their first two or last two source-language characters with text in this "
    "chapter. They are reference-only — included so that shared honorifics, "
    "titles, surnames, and place-name suffixes are translated consistently "
    "across the book. Do not assume \"similar\" entities appear in the chapter, "
    "and do not include them in your response unless you actually encounter "
    "them. Each \"similar\" entry carries a \"match\" field (\"prefix\", "
    "\"suffix\", or \"prefix+suffix\") indicating which part overlapped.\n\n"
    "Your response does NOT use the \"exact\"/\"similar\" split — it uses the "
    "flat per-category structure shown in the template below."
)


def _entry_fields(gendered_categories=None):
    """The per-entity field list. ``gender`` is described against the book's own
    gender-tracked categories rather than the corpus prompts' hardcoded
    "For characters only" — the book column is the real answer.

    ``last_chapter`` is deliberately absent: the contract used to ask for it on
    every entity ("always set to the current chapter number"), which is a field
    the model can only copy from what it was already told, paid for in output
    tokens on every entity of every chunk — and got wrong whenever it echoed the
    template's example value instead. It is stamped in code now, from the chapter
    number the run settled on (``translate_chapter`` / ``extract_entities``), and
    a value the model volunteers anyway is overwritten rather than trusted."""
    gendered = [c for c in (gendered_categories or []) if c]
    if gendered:
        which = ", ".join(f'"{c}"' for c in gendered)
        gender_line = (
            f"- \"gender\": for entities in {which} only. Use \"male\", "
            "\"female\", or \"neutral\". Source-language pronouns are often "
            "ambiguous, so tracking gender keeps pronoun use consistent across "
            "chapters."
        )
    else:
        gender_line = None
    lines = [
        "Each entity entry must include:",
        "- \"translation\": the translated English name.",
    ]
    if gender_line:
        lines.append(gender_line)
    lines += [
        "- \"incorrect_translation\": only when a previous translation was "
        "corrected. Note how it was corrected and use the correct form from "
        "here on.",
        "- \"note\": optional standing guidance for future chapters (see ENTITY "
        "NOTES).",
        "",
        "If a category has no entities, include it as an empty object: "
        "\"characters\": {}",
    ]
    return "\n".join(lines)


def response_contract_section(mode="full", gendered_categories=None,
                              template_json=None, include_example=True):
    """The RESPONSE FORMAT block appended to the translation system prompt.

    ``mode`` mirrors ``generate_system_prompt``: 'full', 'entity_only'
    (entities only — no prose fields) or 'translate_only' (prose only — no
    entity output). ``include_example`` is False for Gemini, whose native
    responseSchema supersedes the worked example (the prose still helps).
    """
    parts = ["RESPONSE FORMAT:", _PROSE_RULES]
    if mode != "translate_only":
        parts += [_ENTITY_KEY_RULE, _EXACT_SIMILAR,
                  _entry_fields(gendered_categories)]
    if include_example and template_json:
        parts.append(f"{TEMPLATE_START}\n{template_json}\n{TEMPLATE_END}")
    return "\n\n".join(parts)


# ── stripping the pre-extraction wording ─────────────────────────────────────
#
# Anchored on the corpus wording, which all 67 frozen templates inherited
# verbatim (measured: the entity-contract prose has 3 variants differing only in
# the trailing bullet wording, and one pattern spans all three). Every pattern
# is independently optional: a prompt that has already been cleaned, or one a
# book hand-rewrote past recognition, is left as it is rather than guessed at.
# The section this module appends comes last in the assembled prompt, so even an
# unstripped leftover is superseded rather than contradictory.

LEGACY_PATTERNS = [
    # The '//' banner introducing the worked example. Stripped before sending
    # either way, but left in place it tells whoever edits the prompt in the book
    # form to preserve a template that is no longer there.
    ("template_comment",
     re.compile(r"^//[ \t]*IMPORTANT: The response template[^\n]*\n"
                r"(?:^//[^\n]*\n)*", re.M)),
    # The worked JSON example, with any comment lines directly above it.
    ("template_block",
     re.compile(r"(?:^//[^\n]*\n)*^\+\+\+\+ Response Template Example\n"
                r".*?^\+\+\+\+ Response Template End[ \t]*\n?",
                re.M | re.S)),
    # CRITICAL RULES bullets that state the output contract.
    ("entity_key_bullet",
     re.compile(r"^- All entity keys in the JSON output MUST be[^\n]*\n", re.M)),
    ("valid_json_bullet",
     re.compile(r"^- Output must be valid JSON[^\n]*\n", re.M)),
    # The entity-note bullets, restated by the code-injected ENTITY NOTES block.
    ("note_bullet",
     re.compile(r"^- You may add a \"note\" to a new entity[^\n]*\n", re.M)),
    ("note_updates_bullet",
     re.compile(r"^- Keeping the note on an entity that already has one[^\n]*\n",
                re.M)),
    # exact/similar explanation through the per-entity field list. Stops before
    # the "If a category has no entities" line, which is stripped separately so
    # a prompt carrying only one of the two is still handled.
    ("entity_contract_prose",
     re.compile(r"^The pre-translated entities block below is split per "
                r"category into two sub-objects:\n"
                r".*?^- \"incorrect_translation\":[^\n]*\n",
                re.M | re.S)),
    ("empty_category_line",
     re.compile(r"^If a category has no entities[^\n]*\n", re.M)),
]

# Patterns every prompt derived from the shipped corpus carries. The rest are
# genuinely optional — the entity-note bullets were added to the corpus late and
# are absent from 44 of the 67 frozen templates, and the '//' banner from more.
# A backfill only needs a human's attention when a CORE section fails to match,
# because that means the book words it differently and may still be carrying it.
CORE_PATTERN_NAMES = frozenset({
    "template_block", "entity_key_bullet", "valid_json_bullet",
    "entity_contract_prose", "empty_category_line",
})

# Three or more blank lines left behind by a removal collapse to two.
_BLANK_RUN = re.compile(r"\n{3,}")
# Removing a whole section can leave its two '---' separators back to back.
_DUP_RULE = re.compile(r"^---[ \t]*\n\s*\n(?=---[ \t]*$)", re.M)


# BOOK-SPECIFIC NOTES is the author's half of the prompt and is never rewritten
# here, not even when it contains contract wording. Six books have the entity-note
# bullets pasted into their notes section; stripping those would be editing the
# user's own text on a guess. The contract section this module appends comes
# last, so a leftover copy up there is superseded, and the backfill reports it
# instead of acting on it. Matches the LAST header, mirroring
# footnote_scan_core.extract_book_notes (a '//'-commented banner is not a header).
_NOTES_HEADER_RE = re.compile(r"^BOOK-SPECIFIC NOTES:?[ \t]*$", re.M)


def split_at_notes(prompt):
    """``(head, notes_tail)`` — the tail starts at the BOOK-SPECIFIC NOTES
    header and is "" when the prompt has none."""
    starts = [m.start() for m in _NOTES_HEADER_RE.finditer(prompt or "")]
    if not starts:
        return prompt or "", ""
    return prompt[:starts[-1]], prompt[starts[-1]:]


def strip_legacy_contract(prompt, report=None):
    """Remove the pre-extraction response-contract wording from a prompt.

    Only the region ABOVE BOOK-SPECIFIC NOTES is touched. Idempotent — a prompt
    with none of it comes back unchanged (modulo whitespace tidying, itself
    idempotent). When ``report`` is a dict it is filled with
    ``{pattern_name: n_removed}`` so the backfill can say what it did and, more
    usefully, what it could not find.
    """
    if not prompt:
        return prompt
    head, tail = split_at_notes(prompt)
    for name, pattern in LEGACY_PATTERNS:
        head, n = pattern.subn("", head)
        if report is not None:
            report[name] = n
    head = _BLANK_RUN.sub("\n\n", head)
    while True:                       # a run of 3+ separators needs more than one pass
        collapsed = _DUP_RULE.sub("", head)
        if collapsed == head:
            break
        head = collapsed
    head = _BLANK_RUN.sub("\n\n", head)
    if tail:
        head = head.rstrip("\n") + "\n\n"
    return head + tail


def has_legacy_contract(prompt):
    """Whether any pre-extraction contract wording survives above the notes."""
    head, _ = split_at_notes(prompt or "")
    return any(p.search(head) for _, p in LEGACY_PATTERNS)


def legacy_in_notes(prompt):
    """Names of contract patterns found INSIDE BOOK-SPECIFIC NOTES.

    Never stripped — reported, so a human decides whether their own note text
    should go."""
    _, tail = split_at_notes(prompt or "")
    return [name for name, p in LEGACY_PATTERNS if tail and p.search(tail)]
