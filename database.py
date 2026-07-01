"""Backward-compatibility shim: the DatabaseManager god class now lives in the
db/ package as a set of repository mixins (db/manager.py assembles them).
Every name historically importable from this module is re-exported here."""

from db import (DatabaseManager, normalize_categories, DEFAULT_CATEGORIES,
                DEFAULT_GENDERED_CATEGORIES, KNOWN_CATEGORY_ATTRIBUTES,
                INCLUDE_SIMILAR_PREFIX)

__all__ = [
    'DatabaseManager', 'normalize_categories', 'DEFAULT_CATEGORIES',
    'DEFAULT_GENDERED_CATEGORIES', 'KNOWN_CATEGORY_ATTRIBUTES',
    'INCLUDE_SIMILAR_PREFIX',
]
