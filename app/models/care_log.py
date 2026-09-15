"""An immutable record of one care action performed on one planting."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from bson import ObjectId


class CareAction(StrEnum):
    WATER = "water"
    FERTILIZE = "fertilize"
    PRUNE = "prune"
    REPOT = "repot"
    HARVEST = "harvest"
    NOTE = "note"

    @property
    def label(self) -> str:
        return {
            CareAction.WATER: "Watered",
            CareAction.FERTILIZE: "Fertilized",
            CareAction.PRUNE: "Pruned",
            CareAction.REPOT: "Repotted",
            CareAction.HARVEST: "Harvested",
            CareAction.NOTE: "Note",
        }[self]

    @property
    def icon(self) -> str:
        return {
            CareAction.WATER: "fa-droplet",
            CareAction.FERTILIZE: "fa-seedling",
            CareAction.PRUNE: "fa-scissors",
            CareAction.REPOT: "fa-box",
            CareAction.HARVEST: "fa-basket-shopping",
            CareAction.NOTE: "fa-pen",
        }[self]

    @classmethod
    def scheduled(cls) -> tuple[CareAction, CareAction]:
        """The two actions that drive reminder scheduling."""
        return (cls.WATER, cls.FERTILIZE)


@dataclass
class CareLog:
    _id: ObjectId
    garden_plant_id: ObjectId
    user_id: ObjectId
    action: str
    note: str | None = None
    # True when the action was recorded after its due date.
    was_late: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def id(self) -> ObjectId:
        return self._id

    @property
    def action_enum(self) -> CareAction:
        try:
            return CareAction(self.action)
        except ValueError:
            return CareAction.NOTE

    @classmethod
    def from_doc(cls, doc: dict[str, Any] | None) -> CareLog | None:
        if not doc:
            return None
        return cls(
            _id=doc["_id"],
            garden_plant_id=doc["garden_plant_id"],
            user_id=doc["user_id"],
            action=doc.get("action", CareAction.NOTE),
            note=doc.get("note"),
            was_late=bool(doc.get("was_late", False)),
            created_at=doc.get("created_at") or datetime.now(UTC),
        )

    def to_doc(self) -> dict[str, Any]:
        return {
            "_id": self._id,
            "garden_plant_id": self.garden_plant_id,
            "user_id": self.user_id,
            "action": str(self.action),
            "note": self.note,
            "was_late": self.was_late,
            "created_at": self.created_at,
        }

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": str(self._id),
            "garden_plant_id": str(self.garden_plant_id),
            "action": str(self.action),
            "label": self.action_enum.label,
            "note": self.note,
            "was_late": self.was_late,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
