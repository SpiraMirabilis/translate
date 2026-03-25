"""Genre preset utilities for the translation tool."""

import json
import os
import re


def load_genres(script_dir):
    """Load genre definitions from genres.json."""
    path = os.path.join(script_dir, "genres.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("genres", [])
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def get_genre(script_dir, genre_id):
    """Look up a single genre by ID. Returns dict or None."""
    for g in load_genres(script_dir):
        if g.get("id") == genre_id:
            return g
    return None


def read_genre_prompt(script_dir, genre):
    """Read the prompt file for a genre (raw, including comment lines). Returns str or None."""
    prompt_file = genre.get("prompt_file")
    if not prompt_file:
        return None
    path = os.path.join(script_dir, prompt_file)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return None


def extract_categories_from_prompt(prompt_text):
    """Extract entity category names from the response template JSON in a prompt.

    Parses the JSON between the ++++ Response Template markers and returns
    the keys of the "entities" object.  Returns None if parsing fails.
    """
    pattern = re.compile(
        r'\+\+\+\+ Response Template Example\n(.*?)\+\+\+\+ Response Template End',
        re.DOTALL,
    )
    match = pattern.search(prompt_text)
    if not match:
        return None
    try:
        template = json.loads(match.group(1).strip())
    except json.JSONDecodeError:
        return None
    entities = template.get("entities")
    if not entities or not isinstance(entities, dict):
        return None
    return list(entities.keys())
