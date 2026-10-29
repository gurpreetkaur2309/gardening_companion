"""Plant embeddings: one vector per species, kept out of the ``plants`` documents.

Vectors are ~768 floats each; storing them inline would add megabytes to every
catalog listing that never looks at them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from bson import ObjectId

from .base import BaseRepository


class EmbeddingRepository(BaseRepository):
    collection_name = "plant_embeddings"

    def hashes(self) -> dict[ObjectId, str]:
        """plant_id -> content hash of the text that was embedded."""
        cursor = self.collection.find({}, {"content_hash": 1, "model": 1, "dimensions": 1})
        return {d["_id"]: f"{d['model']}|{d['dimensions']}|{d['content_hash']}" for d in cursor}

    def upsert_many(
        self, items: list[tuple[ObjectId, str, list[float]]], *, model: str
    ) -> int:
        """``items`` are ``(plant_id, content_hash, vector)``."""
        if not items:
            return 0
        now = datetime.now(UTC)
        # One upsert per species rather than bulk_write: this runs as an
        # occasional batch job, and plain updates behave identically on every
        # backend the test-suite uses.
        for plant_id, content_hash, vector in items:
            self.collection.update_one(
                {"_id": plant_id},
                {
                    "$set": {
                        "content_hash": content_hash,
                        "model": model,
                        "dimensions": len(vector),
                        "vector": vector,
                        "updated_at": now,
                    }
                },
                upsert=True,
            )
        return len(items)

    def all_vectors(self) -> list[dict[str, Any]]:
        return list(self.collection.find({}, {"vector": 1, "dimensions": 1}))

    def fingerprint(self) -> tuple[int, datetime | None]:
        """Cheap change detector for in-process index caches."""
        count = self.collection.count_documents({})
        newest = self.collection.find_one({}, {"updated_at": 1}, sort=[("updated_at", -1)])
        return count, (newest or {}).get("updated_at")

    def delete_orphans(self, live_plant_ids: set[ObjectId]) -> int:
        stale = [i for i in self.hashes() if i not in live_plant_ids]
        if stale:
            self.collection.delete_many({"_id": {"$in": stale}})
        return len(stale)
