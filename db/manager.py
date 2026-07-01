import threading

from db_backend import create_backend

from db.core import DatabaseCore
from db.books_repo import BooksRepo
from db.chapters_repo import ChaptersRepo
from db.entities_repo import EntitiesRepo
from db.queue_repo import QueueRepo
from db.footnotes_repo import FootnotesRepo
from db.comments_repo import CommentsRepo
from db.recommendations_repo import RecommendationsRepo
from db.logs_repo import LogsRepo
from db.wp_repo import WpStateRepo


class DatabaseManager(BooksRepo, ChaptersRepo, EntitiesRepo, QueueRepo,
                      FootnotesRepo, CommentsRepo, RecommendationsRepo,
                      LogsRepo, WpStateRepo, DatabaseCore):
    """Class to manage database operations including entities, books, and chapters using SQLite"""

    def __init__(self, config: 'TranslationConfig', logger: 'Logger', *, strict_writes: bool = False):
        self.config = config
        self.logger = logger
        self.strict_writes = strict_writes
        self.backend = create_backend(config)
        self.db_path = self.backend.db_path  # backward compat for external callers
        self.entities = {}  # Cached entities
        # Guards self.entities against concurrent mutation: the cache is
        # shared between the translation thread, the WP publish thread, and
        # (since handlers moved to the threadpool) request workers. Reentrant
        # because CRUD methods may call _load_entities under the lock.
        self._entities_lock = threading.RLock()
        self._initialize_database()
        self._load_entities()
        self._check_legacy_queue()
