"""Repository base class.

Every collection gets one repository; nothing outside this package talks to
PyMongo.  That keeps query shapes in one place and lets services be tested
against a substitute database without patching driver internals.
"""

from __future__ import annotations

from typing import Any

from pymongo.collection import Collection
from pymongo.database import Database

from ..db import get_db


class BaseRepository:
    """Bind a repository to a collection, optionally on an explicit database.

    In a request or Celery task the database comes from the app context.  Tests
    pass one in directly.
    """

    collection_name: str = ""

    def __init__(self, db: Database | None = None) -> None:
        self._db = db

    @property
    def db(self) -> Database:
        return self._db if self._db is not None else get_db()

    @property
    def collection(self) -> Collection:
        if not self.collection_name:  # pragma: no cover - programming error
            raise NotImplementedError("Subclasses must set collection_name.")
        return self.db[self.collection_name]

    def for_db(self, db: Database) -> BaseRepository:
        """A copy of this repository bound to another database."""
        return type(self)(db)

    # --- Small shared helpers ------------------------------------------------
    def count(self, query: dict[str, Any] | None = None) -> int:
        return self.collection.count_documents(query or {})

    def exists(self, query: dict[str, Any]) -> bool:
        return self.collection.count_documents(query, limit=1) > 0

    def delete_by_id(self, _id: Any) -> bool:
        return self.collection.delete_one({"_id": _id}).deleted_count > 0
