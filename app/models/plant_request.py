"""A user's request for a species missing from the shared catalog."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from bson import ObjectId

from .enums import RequestStatus


@dataclass
class PlantRequest:
    _id: ObjectId
    user_id: ObjectId
    plant_name: str
    description: str | None = None
    status: str = RequestStatus.PENDING
    reviewed_by: ObjectId | None = None
    reviewed_at: datetime | None = None
    review_note: str | None = None
    # Set when an admin approves and the species enters the catalog.
    created_plant_id: ObjectId | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    # Hydrated by the service layer for the admin queue; never persisted.
    requester: Any = None

    @property
    def id(self) -> ObjectId:
        return self._id

    @property
    def is_pending(self) -> bool:
        return self.status == RequestStatus.PENDING

    @classmethod
    def from_doc(cls, doc: dict[str, Any] | None) -> PlantRequest | None:
        if not doc:
            return None
        return cls(
            _id=doc["_id"],
            user_id=doc["user_id"],
            plant_name=doc.get("plant_name", ""),
            description=doc.get("description"),
            status=doc.get("status", RequestStatus.PENDING),
            reviewed_by=doc.get("reviewed_by"),
            reviewed_at=doc.get("reviewed_at"),
            review_note=doc.get("review_note"),
            created_plant_id=doc.get("created_plant_id"),
            created_at=doc.get("created_at") or datetime.now(UTC),
        )

    def to_doc(self) -> dict[str, Any]:
        return {
            "_id": self._id,
            "user_id": self.user_id,
            "plant_name": self.plant_name,
            "description": self.description,
            "status": str(self.status),
            "reviewed_by": self.reviewed_by,
            "reviewed_at": self.reviewed_at,
            "review_note": self.review_note,
            "created_plant_id": self.created_plant_id,
            "created_at": self.created_at,
        }

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": str(self._id),
            "plant_name": self.plant_name,
            "description": self.description,
            "status": str(self.status),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "reviewed_at": self.reviewed_at.isoformat() if self.reviewed_at else None,
            "review_note": self.review_note,
        }
