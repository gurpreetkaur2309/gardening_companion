"""Care history persistence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from bson import ObjectId
from pymongo import DESCENDING

from ..models import CareLog
from ..models.care_log import CareAction
from .base import BaseRepository


class CareLogRepository(BaseRepository):
    collection_name = "care_logs"

    def create(self, log: CareLog) -> CareLog:
        self.collection.insert_one(log.to_doc())
        return log

    def list_for_planting(self, planting_id: ObjectId, *, limit: int = 50) -> list[CareLog]:
        cursor = (
            self.collection.find({"garden_plant_id": planting_id})
            .sort("created_at", DESCENDING)
            .limit(limit)
        )
        return [log for log in (CareLog.from_doc(d) for d in cursor) if log]

    def list_for_user(self, user_id: ObjectId, *, limit: int = 30) -> list[CareLog]:
        cursor = (
            self.collection.find({"user_id": user_id})
            .sort("created_at", DESCENDING)
            .limit(limit)
        )
        return [log for log in (CareLog.from_doc(d) for d in cursor) if log]

    def delete_for_planting(self, planting_id: ObjectId) -> int:
        return self.collection.delete_many({"garden_plant_id": planting_id}).deleted_count

    def delete_all_for_user(self, user_id: ObjectId) -> int:
        return self.collection.delete_many({"user_id": user_id}).deleted_count

    # --- Stats ---------------------------------------------------------------
    def action_counts(self, user_id: ObjectId, *, days: int = 30) -> dict[str, int]:
        since = datetime.now(UTC) - timedelta(days=days)
        pipeline: list[dict[str, Any]] = [
            {"$match": {"user_id": user_id, "created_at": {"$gte": since}}},
            {"$group": {"_id": "$action", "count": {"$sum": 1}}},
        ]
        return {row["_id"]: row["count"] for row in self.collection.aggregate(pipeline)}

    def on_time_rate(self, user_id: ObjectId, *, days: int = 30) -> float | None:
        """Share of scheduled actions logged before they went overdue, 0.0-1.0.

        ``None`` when there is nothing to measure yet.
        """
        since = datetime.now(UTC) - timedelta(days=days)
        pipeline: list[dict[str, Any]] = [
            {
                "$match": {
                    "user_id": user_id,
                    "created_at": {"$gte": since},
                    "action": {"$in": [str(a) for a in CareAction.scheduled()]},
                }
            },
            {
                "$group": {
                    "_id": None,
                    "total": {"$sum": 1},
                    "late": {"$sum": {"$cond": ["$was_late", 1, 0]}},
                }
            },
        ]
        rows = list(self.collection.aggregate(pipeline))
        if not rows or not rows[0].get("total"):
            return None
        row = rows[0]
        return (row["total"] - row["late"]) / row["total"]

    def active_day_streak(self, user_id: ObjectId, *, max_days: int = 365) -> int:
        """Consecutive days, ending today or yesterday, with at least one action.

        Yesterday counts as the anchor so the streak doesn't visibly reset
        first thing each morning before the user has done anything.
        """
        since = datetime.now(UTC) - timedelta(days=max_days)
        pipeline: list[dict[str, Any]] = [
            {"$match": {"user_id": user_id, "created_at": {"$gte": since}}},
            {
                "$group": {
                    "_id": {
                        "$dateToString": {"format": "%Y-%m-%d", "date": "$created_at"}
                    }
                }
            },
            {"$sort": {"_id": DESCENDING}},
        ]
        days = [row["_id"] for row in self.collection.aggregate(pipeline)]
        if not days:
            return 0

        active = {datetime.strptime(d, "%Y-%m-%d").date() for d in days}
        today = datetime.now(UTC).date()
        if today in active:
            cursor_date = today
        elif (today - timedelta(days=1)) in active:
            cursor_date = today - timedelta(days=1)
        else:
            return 0

        streak = 0
        while cursor_date in active:
            streak += 1
            cursor_date -= timedelta(days=1)
        return streak
