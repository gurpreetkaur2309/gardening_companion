"""A named growing space belonging to one user (balcony, terrace, indoor...)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from bson import ObjectId


@dataclass
class Garden:
    _id: ObjectId
    user_id: ObjectId
    name: str
    location: str | None = None
    description: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime | None = None

    @property
    def id(self) -> ObjectId:
        return self._id

    @classmethod
    def from_doc(cls, doc: dict[str, Any] | None) -> Garden | None:
        if not doc:
            return None
        return cls(
            _id=doc["_id"],
            user_id=doc["user_id"],
            name=doc.get("name", "My Garden"),
            location=doc.get("location"),
            description=doc.get("description"),
            created_at=doc.get("created_at") or datetime.now(UTC),
            updated_at=doc.get("updated_at"),
        )

    def to_doc(self) -> dict[str, Any]:
        return {
            "_id": self._id,
            "user_id": self.user_id,
            "name": self.name,
            "location": self.location,
            "description": self.description,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def to_public_dict(self, plant_count: int | None = None) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": str(self._id),
            "name": self.name,
            "location": self.location,
            "description": self.description,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        if plant_count is not None:
            data["plant_count"] = plant_count
        return data
