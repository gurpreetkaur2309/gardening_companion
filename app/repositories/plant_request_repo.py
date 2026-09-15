"""Plant-request queue persistence."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from bson import ObjectId
from pymongo import DESCENDING

from ..models import PlantRequest, RequestStatus
from .base import BaseRepository


class PlantRequestRepository(BaseRepository):
    collection_name = "plant_requests"

    def get(self, request_id: ObjectId) -> PlantRequest | None:
        return PlantRequest.from_doc(self.collection.find_one({"_id": request_id}))

    def create(self, request: PlantRequest) -> PlantRequest:
        self.collection.insert_one(request.to_doc())
        return request

    def list_by_status(
        self, status: str = RequestStatus.PENDING, *, limit: int = 200
    ) -> list[PlantRequest]:
        cursor = (
            self.collection.find({"status": status})
            .sort("created_at", DESCENDING)
            .limit(limit)
        )
        return [r for r in (PlantRequest.from_doc(d) for d in cursor) if r]

    def list_for_user(self, user_id: ObjectId, *, limit: int = 50) -> list[PlantRequest]:
        cursor = (
            self.collection.find({"user_id": user_id})
            .sort("created_at", DESCENDING)
            .limit(limit)
        )
        return [r for r in (PlantRequest.from_doc(d) for d in cursor) if r]

    def has_pending_for_name(self, plant_name: str) -> bool:
        pattern = f"^{re.escape(plant_name.strip())}$"
        return self.exists(
            {
                "status": RequestStatus.PENDING,
                "plant_name": {"$regex": pattern, "$options": "i"},
            }
        )

    def count_pending(self) -> int:
        return self.collection.count_documents({"status": RequestStatus.PENDING})

    def mark_reviewed(
        self,
        request_id: ObjectId,
        *,
        status: str,
        reviewer_id: ObjectId,
        note: str | None = None,
        created_plant_id: ObjectId | None = None,
    ) -> bool:
        fields: dict[str, Any] = {
            "status": status,
            "reviewed_by": reviewer_id,
            "reviewed_at": datetime.now(UTC),
            "review_note": note,
        }
        if created_plant_id is not None:
            fields["created_plant_id"] = created_plant_id
        result = self.collection.update_one({"_id": request_id}, {"$set": fields})
        return result.matched_count > 0

    def delete_all_for_user(self, user_id: ObjectId) -> int:
        return self.collection.delete_many({"user_id": user_id}).deleted_count
