"""Reminder idempotency ledger.

The whole point of this collection is the unique index on
``(garden_plant_id, action, due_on)``.  ``claim()`` inserts a row and reports
whether *this* caller won the race.  A retried Celery task, a second beat
process, or an overlapping sweep therefore cannot email the same user twice
about the same due date — the duplicate insert simply loses.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from .base import BaseRepository


class ReminderRepository(BaseRepository):
    collection_name = "reminder_log"

    def claim(
        self,
        *,
        garden_plant_id: ObjectId,
        action: str,
        due_on: date,
        user_id: ObjectId | None = None,
    ) -> bool:
        """Reserve one (planting, action, due date). True only for the first caller."""
        try:
            self.collection.insert_one(
                {
                    "_id": ObjectId(),
                    "garden_plant_id": garden_plant_id,
                    "action": action,
                    # Stored as a midnight-UTC datetime: BSON has no bare date,
                    # and this keeps one row per calendar due-day.
                    "due_on": datetime(due_on.year, due_on.month, due_on.day, tzinfo=UTC),
                    "user_id": user_id,
                    "sent_at": datetime.now(UTC),
                }
            )
            return True
        except DuplicateKeyError:
            return False

    def was_sent(self, *, garden_plant_id: ObjectId, action: str, due_on: date) -> bool:
        return self.exists(
            {
                "garden_plant_id": garden_plant_id,
                "action": action,
                "due_on": datetime(due_on.year, due_on.month, due_on.day, tzinfo=UTC),
            }
        )

    def release(self, *, garden_plant_id: ObjectId, action: str, due_on: date) -> bool:
        """Undo a claim when the send afterwards failed, so the next sweep retries."""
        result = self.collection.delete_one(
            {
                "garden_plant_id": garden_plant_id,
                "action": action,
                "due_on": datetime(due_on.year, due_on.month, due_on.day, tzinfo=UTC),
            }
        )
        return result.deleted_count > 0

    def recent(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return list(self.collection.find().sort("sent_at", -1).limit(limit))

    def delete_for_planting(self, garden_plant_id: ObjectId) -> int:
        return self.collection.delete_many({"garden_plant_id": garden_plant_id}).deleted_count
