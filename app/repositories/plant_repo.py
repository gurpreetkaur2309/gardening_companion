"""Shared plant catalog persistence."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from bson import ObjectId
from pymongo import ASCENDING
from pymongo.errors import DuplicateKeyError

from ..errors import ConflictError
from ..models import Plant
from ..models.enums import CareSource
from ..models.plant import PlantCare
from .base import BaseRepository


class PlantRepository(BaseRepository):
    collection_name = "plants"

    # --- Lookups -------------------------------------------------------------
    def get(self, plant_id: ObjectId) -> Plant | None:
        return Plant.from_doc(self.collection.find_one({"_id": plant_id}))

    def get_by_common_name(self, name: str) -> Plant | None:
        pattern = f"^{re.escape(name.strip())}$"
        return Plant.from_doc(
            self.collection.find_one({"common_name": {"$regex": pattern, "$options": "i"}})
        )

    def get_many(self, plant_ids: list[ObjectId]) -> dict[ObjectId, Plant]:
        """Batch fetch keyed by id — avoids the N+1 the old dashboard had."""
        if not plant_ids:
            return {}
        cursor = self.collection.find({"_id": {"$in": list(set(plant_ids))}})
        out: dict[ObjectId, Plant] = {}
        for doc in cursor:
            plant = Plant.from_doc(doc)
            if plant:
                out[plant.id] = plant
        return out

    # --- Listing & search ----------------------------------------------------
    def list_all(
        self, *, search: str | None = None, limit: int = 500, skip: int = 0
    ) -> list[Plant]:
        query: dict[str, Any] = {}
        if search and search.strip():
            pattern = re.escape(search.strip())
            # Prefix/substring match beats $text here: users type partial names
            # ("mon" for Monstera) and $text only matches whole words.
            query["$or"] = [
                {"common_name": {"$regex": pattern, "$options": "i"}},
                {"scientific_name": {"$regex": pattern, "$options": "i"}},
                {"genus": {"$regex": pattern, "$options": "i"}},
            ]
        cursor = (
            self.collection.find(query).sort("common_name", ASCENDING).skip(skip).limit(limit)
        )
        return [p for p in (Plant.from_doc(d) for d in cursor) if p]

    def list_choices(self) -> list[tuple[str, str]]:
        """Lightweight (id, name) pairs for the add-plant picker."""
        cursor = self.collection.find({}, {"common_name": 1}).sort("common_name", ASCENDING)
        return [(str(d["_id"]), d.get("common_name", "Unknown")) for d in cursor]

    # --- Writes --------------------------------------------------------------
    def create(self, plant: Plant) -> Plant:
        try:
            self.collection.insert_one(plant.to_doc())
        except DuplicateKeyError as exc:
            raise ConflictError(
                f"A plant with the scientific name '{plant.scientific_name}' already exists."
            ) from exc
        return plant

    def update_fields(self, plant_id: ObjectId, fields: dict[str, Any]) -> bool:
        fields = {**fields, "updated_at": datetime.now(UTC)}
        try:
            result = self.collection.update_one({"_id": plant_id}, {"$set": fields})
        except DuplicateKeyError as exc:
            raise ConflictError("Another plant already uses that scientific name.") from exc
        return result.matched_count > 0

    def cache_care(self, plant_id: ObjectId, care: PlantCare, source: str = CareSource.AI) -> bool:
        """Persist an inferred schedule so the species is never looked up twice."""
        return self.update_fields(plant_id, {"care": care.to_doc(), "care_source": str(source)})

    def set_image_key(self, plant_id: ObjectId, image_key: str | None) -> bool:
        return self.update_fields(plant_id, {"image_key": image_key})

    def delete(self, plant_id: ObjectId) -> bool:
        return self.delete_by_id(plant_id)

    def upsert_by_scientific_name(self, plant: Plant) -> tuple[Plant, bool]:
        """Insert, or update in place if the species is already catalogued.

        Used by the seeder so re-running it is safe.  Returns ``(plant, created)``.
        """
        key = plant.scientific_name
        if not key:
            return self.create(plant), True
        existing = self.collection.find_one({"scientific_name": key})
        if existing:
            doc = plant.to_doc()
            doc.pop("_id", None)
            doc.pop("created_at", None)
            # Never clobber a real uploaded image with a seed file's blank.
            if not doc.get("image_key"):
                doc.pop("image_key", None)
            self.collection.update_one({"_id": existing["_id"]}, {"$set": doc})
            refreshed = Plant.from_doc(self.collection.find_one({"_id": existing["_id"]}))
            return refreshed or plant, False
        return self.create(plant), True
