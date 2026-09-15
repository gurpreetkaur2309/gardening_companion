"""Garden persistence. Every query is scoped by ``user_id``."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from bson import ObjectId
from pymongo import ASCENDING
from pymongo.errors import DuplicateKeyError

from ..errors import ConflictError
from ..models import Garden
from .base import BaseRepository


class GardenRepository(BaseRepository):
    collection_name = "gardens"

    def get(self, garden_id: ObjectId, user_id: ObjectId) -> Garden | None:
        """Ownership is part of the query, not a check after the fact."""
        return Garden.from_doc(
            self.collection.find_one({"_id": garden_id, "user_id": user_id})
        )

    def list_for_user(self, user_id: ObjectId) -> list[Garden]:
        cursor = self.collection.find({"user_id": user_id}).sort("created_at", ASCENDING)
        return [g for g in (Garden.from_doc(d) for d in cursor) if g]

    def first_for_user(self, user_id: ObjectId) -> Garden | None:
        return Garden.from_doc(
            self.collection.find_one({"user_id": user_id}, sort=[("created_at", ASCENDING)])
        )

    def create(self, garden: Garden) -> Garden:
        try:
            self.collection.insert_one(garden.to_doc())
        except DuplicateKeyError as exc:
            raise ConflictError(f"You already have a garden called '{garden.name}'.") from exc
        return garden

    def update_fields(self, garden_id: ObjectId, user_id: ObjectId, fields: dict[str, Any]) -> bool:
        fields = {**fields, "updated_at": datetime.now(UTC)}
        try:
            result = self.collection.update_one(
                {"_id": garden_id, "user_id": user_id}, {"$set": fields}
            )
        except DuplicateKeyError as exc:
            raise ConflictError("You already have a garden with that name.") from exc
        return result.matched_count > 0

    def delete(self, garden_id: ObjectId, user_id: ObjectId) -> bool:
        return self.collection.delete_one({"_id": garden_id, "user_id": user_id}).deleted_count > 0

    def count_for_user(self, user_id: ObjectId) -> int:
        return self.collection.count_documents({"user_id": user_id})

    def delete_all_for_user(self, user_id: ObjectId) -> int:
        return self.collection.delete_many({"user_id": user_id}).deleted_count
