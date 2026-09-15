"""Planting persistence, including the due-date queries the reminder sweep runs."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from bson import ObjectId
from pymongo import ASCENDING

from ..models import GardenPlant
from .base import BaseRepository


class GardenPlantRepository(BaseRepository):
    collection_name = "garden_plants"

    # --- Lookups -------------------------------------------------------------
    def get(self, planting_id: ObjectId, user_id: ObjectId) -> GardenPlant | None:
        return GardenPlant.from_doc(
            self.collection.find_one({"_id": planting_id, "user_id": user_id})
        )

    def get_any(self, planting_id: ObjectId) -> GardenPlant | None:
        """Unscoped — for Celery tasks that already resolved the owner."""
        return GardenPlant.from_doc(self.collection.find_one({"_id": planting_id}))

    def list_for_garden(self, garden_id: ObjectId, user_id: ObjectId) -> list[GardenPlant]:
        cursor = self.collection.find({"garden_id": garden_id, "user_id": user_id}).sort(
            "created_at", ASCENDING
        )
        return [p for p in (GardenPlant.from_doc(d) for d in cursor) if p]

    def list_for_user(self, user_id: ObjectId) -> list[GardenPlant]:
        cursor = self.collection.find({"user_id": user_id}).sort("next_water_due", ASCENDING)
        return [p for p in (GardenPlant.from_doc(d) for d in cursor) if p]

    def find_existing(
        self, *, user_id: ObjectId, garden_id: ObjectId, plant_id: ObjectId, age_stage: str
    ) -> GardenPlant | None:
        """Same species at the same life stage in the same garden — bump quantity."""
        return GardenPlant.from_doc(
            self.collection.find_one(
                {
                    "user_id": user_id,
                    "garden_id": garden_id,
                    "plant_id": plant_id,
                    "age_stage": age_stage,
                }
            )
        )

    # --- Writes --------------------------------------------------------------
    def create(self, planting: GardenPlant) -> GardenPlant:
        self.collection.insert_one(planting.to_doc())
        return planting

    def update_fields(
        self, planting_id: ObjectId, user_id: ObjectId, fields: dict[str, Any]
    ) -> bool:
        fields = {**fields, "updated_at": datetime.now(UTC)}
        result = self.collection.update_one(
            {"_id": planting_id, "user_id": user_id}, {"$set": fields}
        )
        return result.matched_count > 0

    def adjust_quantity(self, planting_id: ObjectId, user_id: ObjectId, delta: int) -> int | None:
        """Change quantity, deleting the row if it would reach zero.

        Returns the new quantity, or ``None`` when the planting was removed.
        """
        doc = self.collection.find_one({"_id": planting_id, "user_id": user_id})
        if not doc:
            return None
        new_quantity = int(doc.get("quantity", 1)) + delta
        if new_quantity <= 0:
            self.collection.delete_one({"_id": planting_id, "user_id": user_id})
            return None
        self.collection.update_one(
            {"_id": planting_id, "user_id": user_id},
            {"$set": {"quantity": new_quantity, "updated_at": datetime.now(UTC)}},
        )
        return new_quantity

    def delete(self, planting_id: ObjectId, user_id: ObjectId) -> bool:
        return (
            self.collection.delete_one({"_id": planting_id, "user_id": user_id}).deleted_count > 0
        )

    def delete_for_garden(self, garden_id: ObjectId, user_id: ObjectId) -> int:
        return self.collection.delete_many(
            {"garden_id": garden_id, "user_id": user_id}
        ).deleted_count

    def delete_all_for_user(self, user_id: ObjectId) -> int:
        return self.collection.delete_many({"user_id": user_id}).deleted_count

    def count_using_plant(self, plant_id: ObjectId) -> int:
        """How many plantings reference a catalog species — checked before delete."""
        return self.collection.count_documents({"plant_id": plant_id})

    # --- Due-date queries ----------------------------------------------------
    def list_due(self, *, now: datetime | None = None, limit: int = 5000) -> list[GardenPlant]:
        """Every planting with a watering or fertilizing task at or past due.

        Backed by the ``idx_water_due`` / ``idx_fertilize_due`` indexes.
        """
        moment = now or datetime.now(UTC)
        cursor = (
            self.collection.find(
                {
                    "reminders_enabled": True,
                    "$or": [
                        {"next_water_due": {"$lte": moment}},
                        {"next_fertilize_due": {"$lte": moment}},
                    ],
                }
            )
            .sort("user_id", ASCENDING)
            .limit(limit)
        )
        return [p for p in (GardenPlant.from_doc(d) for d in cursor) if p]

    def list_due_for_user(
        self, user_id: ObjectId, *, now: datetime | None = None
    ) -> list[GardenPlant]:
        moment = now or datetime.now(UTC)
        cursor = self.collection.find(
            {
                "user_id": user_id,
                "reminders_enabled": True,
                "$or": [
                    {"next_water_due": {"$lte": moment}},
                    {"next_fertilize_due": {"$lte": moment}},
                ],
            }
        ).sort("next_water_due", ASCENDING)
        return [p for p in (GardenPlant.from_doc(d) for d in cursor) if p]

    # --- Aggregates ----------------------------------------------------------
    def count_by_garden(self, user_id: ObjectId) -> dict[ObjectId, int]:
        """Planting counts per garden, for the garden switcher badges."""
        pipeline: list[dict[str, Any]] = [
            {"$match": {"user_id": user_id}},
            {"$group": {"_id": "$garden_id", "count": {"$sum": "$quantity"}}},
        ]
        return {row["_id"]: row["count"] for row in self.collection.aggregate(pipeline)}

    def user_stats(self, user_id: ObjectId, *, now: datetime | None = None) -> dict[str, int]:
        """Headline dashboard numbers in a single round trip."""
        moment = now or datetime.now(UTC)
        pipeline: list[dict[str, Any]] = [
            {"$match": {"user_id": user_id}},
            {
                "$group": {
                    "_id": None,
                    "plantings": {"$sum": 1},
                    "total_plants": {"$sum": "$quantity"},
                    "species": {"$addToSet": "$plant_id"},
                    "needs_water": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$and": [
                                        "$reminders_enabled",
                                        {"$lte": ["$next_water_due", moment]},
                                    ]
                                },
                                1,
                                0,
                            ]
                        }
                    },
                    "needs_fertilizer": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$and": [
                                        "$reminders_enabled",
                                        {"$lte": ["$next_fertilize_due", moment]},
                                    ]
                                },
                                1,
                                0,
                            ]
                        }
                    },
                }
            },
        ]
        rows = list(self.collection.aggregate(pipeline))
        if not rows:
            return {
                "plantings": 0,
                "total_plants": 0,
                "species": 0,
                "needs_water": 0,
                "needs_fertilizer": 0,
            }
        row = rows[0]
        return {
            "plantings": row.get("plantings", 0),
            "total_plants": row.get("total_plants", 0),
            "species": len(row.get("species", [])),
            "needs_water": row.get("needs_water", 0),
            "needs_fertilizer": row.get("needs_fertilizer", 0),
        }
