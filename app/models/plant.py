"""The shared plant catalog: one document per species."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from bson import ObjectId

from .enums import CareSource, Sunlight


@dataclass
class PlantCare:
    """Baseline care for a mature specimen of a species.

    Intervals are in days.  ``GardenPlant`` scales these by life stage.
    """

    watering_days: int = 7
    fertilizing_days: int = 30
    sunlight: str = Sunlight.PARTIAL
    fertilizer_type: str = "Balanced 10-10-10"
    notes: str | None = None

    @classmethod
    def from_doc(cls, doc: dict[str, Any] | None) -> PlantCare:
        doc = doc or {}
        return cls(
            watering_days=int(doc.get("watering_days") or 7),
            fertilizing_days=int(doc.get("fertilizing_days") or 30),
            sunlight=doc.get("sunlight") or Sunlight.PARTIAL,
            fertilizer_type=doc.get("fertilizer_type") or "Balanced 10-10-10",
            notes=doc.get("notes"),
        )

    def to_doc(self) -> dict[str, Any]:
        return {
            "watering_days": self.watering_days,
            "fertilizing_days": self.fertilizing_days,
            "sunlight": str(self.sunlight),
            "fertilizer_type": self.fertilizer_type,
            "notes": self.notes,
        }

    @property
    def sunlight_enum(self) -> Sunlight:
        try:
            return Sunlight(self.sunlight)
        except ValueError:
            return Sunlight.PARTIAL


@dataclass
class Plant:
    """A species in the catalog, shared across all users."""

    _id: ObjectId
    common_name: str
    scientific_name: str | None = None
    family: str | None = None
    genus: str | None = None
    rank: str | None = None
    edible: bool = False
    description: str | None = None
    sapling_description: str | None = None
    # Storage key (S3 object key or local relative path). Resolved to a URL at
    # render time by the storage service — never store a presigned URL.
    image_key: str | None = None
    care: PlantCare = field(default_factory=PlantCare)
    care_source: str = CareSource.DEFAULT
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime | None = None

    @property
    def id(self) -> ObjectId:
        return self._id

    @property
    def has_care_data(self) -> bool:
        """True once the schedule is real, not the fallback default."""
        return self.care_source in {CareSource.CURATED, CareSource.AI, CareSource.MANUAL}

    @property
    def display_subtitle(self) -> str:
        return self.scientific_name or self.family or self.genus or ""

    @classmethod
    def from_doc(cls, doc: dict[str, Any] | None) -> Plant | None:
        if not doc:
            return None
        return cls(
            _id=doc["_id"],
            common_name=doc.get("common_name", "Unknown"),
            scientific_name=doc.get("scientific_name"),
            family=doc.get("family"),
            genus=doc.get("genus"),
            rank=doc.get("rank"),
            edible=bool(doc.get("edible", False)),
            description=doc.get("description"),
            sapling_description=doc.get("sapling_description"),
            image_key=doc.get("image_key"),
            care=PlantCare.from_doc(doc.get("care")),
            care_source=doc.get("care_source", CareSource.DEFAULT),
            created_at=doc.get("created_at") or datetime.now(UTC),
            updated_at=doc.get("updated_at"),
        )

    def to_doc(self) -> dict[str, Any]:
        return {
            "_id": self._id,
            "common_name": self.common_name,
            "scientific_name": self.scientific_name,
            "family": self.family,
            "genus": self.genus,
            "rank": self.rank,
            "edible": self.edible,
            "description": self.description,
            "sapling_description": self.sapling_description,
            "image_key": self.image_key,
            "care": self.care.to_doc(),
            "care_source": str(self.care_source),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def to_public_dict(self, image_url: str | None = None) -> dict[str, Any]:
        return {
            "id": str(self._id),
            "common_name": self.common_name,
            "scientific_name": self.scientific_name,
            "family": self.family,
            "genus": self.genus,
            "edible": self.edible,
            "description": self.description,
            "image_url": image_url,
            "care": self.care.to_doc(),
            "care_source": str(self.care_source),
        }
