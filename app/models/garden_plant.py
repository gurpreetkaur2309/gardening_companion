"""A planting: one species, in one garden, belonging to one user.

This is where care scheduling actually lives.  The species catalog supplies a
baseline interval; the planting stores the *effective* schedule (baseline scaled
by life stage, or overridden by the user) plus the last-done and next-due
timestamps that the dashboard and the reminder sweep both read.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from bson import ObjectId

from .enums import AgeStage, Sunlight
from .plant import PlantCare


@dataclass
class CareSchedule:
    """The effective schedule for one planting, in days."""

    watering_days: int = 7
    fertilizing_days: int = 30
    sunlight: str = Sunlight.PARTIAL
    fertilizer_type: str = "Balanced 10-10-10"

    # Minimum sane intervals — an LLM or a typo must not produce "water every
    # 0 days", which would make the reminder sweep fire on every run.
    MIN_WATERING_DAYS = 1
    MAX_WATERING_DAYS = 120
    MIN_FERTILIZING_DAYS = 3
    MAX_FERTILIZING_DAYS = 365

    def __post_init__(self) -> None:
        self.watering_days = max(
            self.MIN_WATERING_DAYS, min(int(self.watering_days), self.MAX_WATERING_DAYS)
        )
        self.fertilizing_days = max(
            self.MIN_FERTILIZING_DAYS,
            min(int(self.fertilizing_days), self.MAX_FERTILIZING_DAYS),
        )

    @classmethod
    def for_stage(cls, care: PlantCare, stage: AgeStage) -> CareSchedule:
        """Scale a species baseline down for younger, thirstier plants."""
        factor = stage.interval_multiplier
        return cls(
            watering_days=round(care.watering_days * factor),
            fertilizing_days=round(care.fertilizing_days * factor),
            sunlight=str(care.sunlight),
            fertilizer_type=care.fertilizer_type,
        )

    @classmethod
    def from_doc(cls, doc: dict[str, Any] | None) -> CareSchedule:
        doc = doc or {}
        return cls(
            watering_days=int(doc.get("watering_days") or 7),
            fertilizing_days=int(doc.get("fertilizing_days") or 30),
            sunlight=doc.get("sunlight") or Sunlight.PARTIAL,
            fertilizer_type=doc.get("fertilizer_type") or "Balanced 10-10-10",
        )

    def to_doc(self) -> dict[str, Any]:
        return {
            "watering_days": self.watering_days,
            "fertilizing_days": self.fertilizing_days,
            "sunlight": str(self.sunlight),
            "fertilizer_type": self.fertilizer_type,
        }

    @property
    def sunlight_enum(self) -> Sunlight:
        try:
            return Sunlight(self.sunlight)
        except ValueError:
            return Sunlight.PARTIAL


@dataclass
class GardenPlant:
    _id: ObjectId
    user_id: ObjectId
    garden_id: ObjectId
    plant_id: ObjectId
    nickname: str | None = None
    age_stage: str = AgeStage.MATURE
    quantity: int = 1
    planted_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    schedule: CareSchedule = field(default_factory=CareSchedule)
    last_watered_at: datetime | None = None
    last_fertilized_at: datetime | None = None
    next_water_due: datetime | None = None
    next_fertilize_due: datetime | None = None
    reminders_enabled: bool = True
    notes: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime | None = None

    # Hydrated by the service layer when a plant is joined in; never persisted.
    plant: Any = None

    @property
    def id(self) -> ObjectId:
        return self._id

    @property
    def age_stage_enum(self) -> AgeStage:
        try:
            return AgeStage(self.age_stage)
        except ValueError:
            return AgeStage.MATURE

    @property
    def display_name(self) -> str:
        if self.nickname:
            return self.nickname
        if self.plant is not None:
            return self.plant.common_name
        return "Plant"

    # --- Due-date arithmetic -------------------------------------------------
    def compute_next_water_due(self) -> datetime:
        base = self.last_watered_at or self.planted_at
        return base + timedelta(days=self.schedule.watering_days)

    def compute_next_fertilize_due(self) -> datetime:
        base = self.last_fertilized_at or self.planted_at
        return base + timedelta(days=self.schedule.fertilizing_days)

    def refresh_due_dates(self) -> None:
        self.next_water_due = self.compute_next_water_due()
        self.next_fertilize_due = self.compute_next_fertilize_due()

    def days_until(self, due: datetime | None, *, now: datetime | None = None) -> int | None:
        """Whole days until ``due``. Negative means overdue; None if unscheduled."""
        if due is None:
            return None
        current = now or datetime.now(UTC)
        if due.tzinfo is None:
            due = due.replace(tzinfo=UTC)
        delta = due.date() - current.date()
        return delta.days

    @property
    def days_until_water(self) -> int | None:
        return self.days_until(self.next_water_due)

    @property
    def days_until_fertilize(self) -> int | None:
        return self.days_until(self.next_fertilize_due)

    def is_due(self, due: datetime | None, *, now: datetime | None = None) -> bool:
        days = self.days_until(due, now=now)
        return days is not None and days <= 0

    @property
    def needs_water(self) -> bool:
        return self.reminders_enabled and self.is_due(self.next_water_due)

    @property
    def needs_fertilizer(self) -> bool:
        return self.reminders_enabled and self.is_due(self.next_fertilize_due)

    @property
    def urgency(self) -> str:
        """``overdue`` | ``due`` | ``upcoming`` — drives the dashboard grouping."""
        candidates = [
            d
            for d in (self.days_until_water, self.days_until_fertilize)
            if d is not None
        ]
        if not self.reminders_enabled or not candidates:
            return "upcoming"
        soonest = min(candidates)
        if soonest < 0:
            return "overdue"
        if soonest == 0:
            return "due"
        return "upcoming"

    # --- Serialisation -------------------------------------------------------
    @classmethod
    def from_doc(cls, doc: dict[str, Any] | None) -> GardenPlant | None:
        if not doc:
            return None
        return cls(
            _id=doc["_id"],
            user_id=doc["user_id"],
            garden_id=doc["garden_id"],
            plant_id=doc["plant_id"],
            nickname=doc.get("nickname"),
            age_stage=doc.get("age_stage", AgeStage.MATURE),
            quantity=int(doc.get("quantity") or 1),
            planted_at=doc.get("planted_at") or doc.get("created_at") or datetime.now(UTC),
            schedule=CareSchedule.from_doc(doc.get("schedule")),
            last_watered_at=doc.get("last_watered_at"),
            last_fertilized_at=doc.get("last_fertilized_at"),
            next_water_due=doc.get("next_water_due"),
            next_fertilize_due=doc.get("next_fertilize_due"),
            reminders_enabled=bool(doc.get("reminders_enabled", True)),
            notes=doc.get("notes"),
            created_at=doc.get("created_at") or datetime.now(UTC),
            updated_at=doc.get("updated_at"),
        )

    def to_doc(self) -> dict[str, Any]:
        return {
            "_id": self._id,
            "user_id": self.user_id,
            "garden_id": self.garden_id,
            "plant_id": self.plant_id,
            "nickname": self.nickname,
            "age_stage": str(self.age_stage),
            "quantity": self.quantity,
            "planted_at": self.planted_at,
            "schedule": self.schedule.to_doc(),
            "last_watered_at": self.last_watered_at,
            "last_fertilized_at": self.last_fertilized_at,
            "next_water_due": self.next_water_due,
            "next_fertilize_due": self.next_fertilize_due,
            "reminders_enabled": self.reminders_enabled,
            "notes": self.notes,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def to_public_dict(self, image_url: str | None = None) -> dict[str, Any]:
        return {
            "id": str(self._id),
            "garden_id": str(self.garden_id),
            "plant_id": str(self.plant_id),
            "name": self.display_name,
            "species": self.plant.common_name if self.plant else None,
            "scientific_name": self.plant.scientific_name if self.plant else None,
            "nickname": self.nickname,
            "age_stage": str(self.age_stage),
            "quantity": self.quantity,
            "image_url": image_url,
            "schedule": self.schedule.to_doc(),
            "planted_at": self.planted_at.isoformat() if self.planted_at else None,
            "last_watered_at": (
                self.last_watered_at.isoformat() if self.last_watered_at else None
            ),
            "last_fertilized_at": (
                self.last_fertilized_at.isoformat() if self.last_fertilized_at else None
            ),
            "next_water_due": self.next_water_due.isoformat() if self.next_water_due else None,
            "next_fertilize_due": (
                self.next_fertilize_due.isoformat() if self.next_fertilize_due else None
            ),
            "days_until_water": self.days_until_water,
            "days_until_fertilize": self.days_until_fertilize,
            "urgency": self.urgency,
            "reminders_enabled": self.reminders_enabled,
        }
