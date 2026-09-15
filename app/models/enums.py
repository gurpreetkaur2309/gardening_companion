"""Domain enumerations.

All are ``str`` enums so they serialise straight into Mongo and Jinja without
conversion, and compare equal to the plain strings already stored.
"""

from __future__ import annotations

from enum import StrEnum


class UserRole(StrEnum):
    USER = "user"
    ADMIN = "admin"


class UserStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


class AgeStage(StrEnum):
    """Life stage of a planting. Drives the care-interval multiplier."""

    SEEDLING = "seedling"
    YOUNG = "young"
    MATURE = "mature"

    @property
    def label(self) -> str:
        return {
            AgeStage.SEEDLING: "Seedling (0-3 months)",
            AgeStage.YOUNG: "Young (3-12 months)",
            AgeStage.MATURE: "Mature (1 year+)",
        }[self]

    @property
    def interval_multiplier(self) -> float:
        """Young plants need water more often; roots are shallow and small.

        Applied to the species' baseline interval, so a snake plant watered
        every 21 days as a mature plant is watered every ~11 days as a seedling.
        """
        return {
            AgeStage.SEEDLING: 0.5,
            AgeStage.YOUNG: 0.75,
            AgeStage.MATURE: 1.0,
        }[self]


class Sunlight(StrEnum):
    FULL = "full"
    PARTIAL = "partial"
    SHADE = "shade"

    @property
    def label(self) -> str:
        return {
            Sunlight.FULL: "Full sun (6+ hrs direct)",
            Sunlight.PARTIAL: "Partial sun (3-6 hrs)",
            Sunlight.SHADE: "Shade (indirect light)",
        }[self]

    @property
    def icon(self) -> str:
        return {
            Sunlight.FULL: "fa-sun",
            Sunlight.PARTIAL: "fa-cloud-sun",
            Sunlight.SHADE: "fa-cloud",
        }[self]


class RequestStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class CareSource(StrEnum):
    """Where a plant's care schedule came from — shown in the admin catalog."""

    CURATED = "curated"
    AI = "ai"
    DEFAULT = "default"
    MANUAL = "manual"
