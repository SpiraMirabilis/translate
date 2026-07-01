import json
import os
import sqlite3
from contextlib import contextmanager

DEFAULT_CATEGORIES = [
    'characters', 'places', 'organizations', 'abilities',
    'titles', 'equipment', 'creatures'
]

# Categories that carry a gender attribute by default (and the attribute set they
# get). "characters" has always been gender-tracked; this is the seed used when a
# book stores no explicit per-category attributes (legacy string lists / NULL).
DEFAULT_GENDERED_CATEGORIES = {'characters'}

# Known per-category attributes. Today only "gender" exists, but categories are
# stored as {"name": ..., "attributes": [...]} so new behaviours can be added
# without a schema change.
KNOWN_CATEGORY_ATTRIBUTES = {'gender'}


def normalize_categories(raw):
    """Normalize a book's stored categories into a list of attribute-carrying dicts.

    Accepts any of:
      - None                          -> DEFAULT_CATEGORIES (characters gendered)
      - ["characters", "places"]      -> legacy string list (characters gendered)
      - [{"name": ..., "attributes": [...]}]  -> already-normalized objects

    Always returns a list of {"name": str, "attributes": [str, ...]} dicts.
    Attribute lists are de-duplicated and filtered to KNOWN_CATEGORY_ATTRIBUTES.
    """
    if raw is None:
        raw = list(DEFAULT_CATEGORIES)

    out = []
    seen = set()
    for item in raw:
        if isinstance(item, str):
            name = item.strip()
            attrs = ['gender'] if name in DEFAULT_GENDERED_CATEGORIES else []
        elif isinstance(item, dict):
            name = str(item.get('name', '')).strip()
            attrs = item.get('attributes') or []
            if isinstance(attrs, str):
                attrs = [attrs]
        else:
            continue
        if not name or name in seen:
            continue
        seen.add(name)
        clean_attrs = [a for a in dict.fromkeys(attrs) if a in KNOWN_CATEGORY_ATTRIBUTES]
        out.append({'name': name, 'attributes': clean_attrs})
    return out

# Pre-translation similarity matching: include entities whose first-2 or last-2
# source-language chars appear in the chapter text, as reference-only hints for
# naming-style consistency. Suffix matching is the strongest signal (cultivation
# titles like 真君, 道人); prefix matching is noisier (common chars like 大, 小).
# Flip INCLUDE_SIMILAR_PREFIX to False to drop the prefix half if it adds noise
# without benefit.
INCLUDE_SIMILAR_PREFIX = True


class DatabaseCore:
    """Connection lifecycle, schema init, and small shared JSON-file utilities."""

    def get_connection(self):
        """Return a new database connection via the configured backend."""
        return self.backend.get_connection()

    @contextmanager
    def _conn(self, dict_rows: bool = False):
        """Yield a connection; commit on clean exit, rollback on exception,
        always close (for MySQL, close returns the connection to the pool).

        This is the standard connection scope for DatabaseManager methods —
        it guarantees no leaked connections and no half-committed writes on
        the error path. dict_rows=True sets sqlite3.Row (the MySQL wrapper
        honors row_factory equivalently).
        """
        conn = self.backend.get_connection()
        if dict_rows:
            conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            conn.close()

    def _initialize_database(self):
        """Initialize the database schema via the versioned migration runner."""
        try:
            from db.migrations import run_migrations
            run_migrations(self.backend, self.logger)

            # Create covers + illustrations directories (only meaningful for local installs)
            if self.backend.name == 'sqlite':
                media_base = os.path.dirname(self.db_path)
            else:
                media_base = self.config.script_dir
            os.makedirs(os.path.join(media_base, "covers"), exist_ok=True)
            os.makedirs(os.path.join(media_base, "illustrations"), exist_ok=True)

            self.logger.info("Database initialized successfully")
        except Exception as e:
            self.logger.error(f"Database initialization error: {e}")
            raise

    def _check_legacy_queue(self):
        """Check for legacy queue.json and warn user"""
        queue_path = os.path.join(self.config.script_dir, "queue.json")
        if os.path.exists(queue_path):
            try:
                with open(queue_path, 'r', encoding='utf-8') as f:
                    legacy_queue = json.load(f)

                if legacy_queue and len(legacy_queue) > 0:
                    self.logger.warning(f"Found legacy queue.json with {len(legacy_queue)} items")
                    print("\n" + "="*60)
                    print("WARNING: Legacy queue.json detected")
                    print("="*60)
                    print(f"Found {len(legacy_queue)} items in old queue.json.")
                    print("The queue system now uses the database.")
                    print("\nYour old queue.json will NOT be processed automatically.")
                    print("\nOptions:")
                    print("  1. Process old queue first:")
                    print("     python translator.py --resume  (repeat until empty)")
                    print("  2. Clear old queue:")
                    print("     rm queue.json")
                    print("  3. Ignore - items will not be processed")
                    print("="*60 + "\n")
            except Exception as e:
                self.logger.debug(f"Error checking legacy queue: {e}")

    def _load_json_file(self, filepath, default=None):
        """Load JSON data from a file with error handling"""
        full_path = os.path.join(self.config.script_dir, filepath)
        
        if not os.path.exists(full_path):
            return default or {}
        
        try:
            with open(full_path, 'r', encoding='utf-8') as file:
                return json.load(file)
        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to decode JSON from file '{filepath}': {e}")
            return default or {}
        except OSError as e:
            self.logger.error(f"Failed to read file '{filepath}': {e}")
            return default or {}

    def save_json_file(self, filepath, data):
        """Save data to a JSON file with error handling"""
        full_path = os.path.join(self.config.script_dir, filepath)
        
        try:
            with open(full_path, 'w', encoding='utf-8') as file:
                json.dump(data, file, indent=4, ensure_ascii=False)
        except OSError as e:
            self.logger.error(f"Failed to write to file '{filepath}': {e}")
