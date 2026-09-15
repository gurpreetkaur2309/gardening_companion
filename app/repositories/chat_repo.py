"""Chat transcript persistence.

Messages carry a TTL index (90 days, see ``app/db.py``) so transcripts expire
on their own instead of growing forever.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from bson import ObjectId
from pymongo import ASCENDING, DESCENDING

from .base import BaseRepository

# Roles as stored. "model" matches what the Gemini SDK expects back as history.
ROLE_USER = "user"
ROLE_ASSISTANT = "model"


class ChatRepository(BaseRepository):
    collection_name = "chat_messages"

    def add(self, user_id: ObjectId, role: str, content: str) -> dict[str, Any]:
        doc = {
            "_id": ObjectId(),
            "user_id": user_id,
            "role": role,
            "content": content,
            "created_at": datetime.now(UTC),
        }
        self.collection.insert_one(doc)
        return doc

    # A question and its answer are usually written within the same millisecond,
    # and MongoDB stores datetimes only to that resolution — so ``created_at``
    # alone leaves their order undefined and the transcript can render the reply
    # above the question. ObjectIds increase monotonically within a process, so
    # _id is the tiebreaker.
    _OLDEST_FIRST = [("created_at", ASCENDING), ("_id", ASCENDING)]
    _NEWEST_FIRST = [("created_at", DESCENDING), ("_id", DESCENDING)]

    def history(self, user_id: ObjectId, *, limit: int = 20) -> list[dict[str, Any]]:
        """The last ``limit`` messages, oldest first (the order a model wants)."""
        cursor = (
            self.collection.find({"user_id": user_id})
            .sort(self._NEWEST_FIRST)
            .limit(limit)
        )
        return list(reversed(list(cursor)))

    def full_history(self, user_id: ObjectId, *, limit: int = 200) -> list[dict[str, Any]]:
        cursor = (
            self.collection.find({"user_id": user_id})
            .sort(self._OLDEST_FIRST)
            .limit(limit)
        )
        return list(cursor)

    def clear(self, user_id: ObjectId) -> int:
        return self.collection.delete_many({"user_id": user_id}).deleted_count

    def count_since(self, user_id: ObjectId, since: datetime) -> int:
        return self.collection.count_documents(
            {"user_id": user_id, "role": ROLE_USER, "created_at": {"$gte": since}}
        )
