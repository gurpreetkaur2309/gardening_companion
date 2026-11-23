"""Load: write the final catalog file, then (optionally) upsert into MongoDB.

The Mongo upsert is *fill-only* for species that already exist: curated care
data and admin edits must never be overwritten by a re-scrape.
"""

from __future__ import annotations

import json
from pathlib import Path

from .records import PlantRecord


def write_catalog(
    path: Path, records: list[PlantRecord], *, exclude: set[str] | None = None
) -> int:
    """Write ``records`` sorted by name, skipping species already curated elsewhere."""
    exclude = {e.lower() for e in (exclude or set())}
    kept = [r for r in records if r.scientific_name.lower() not in exclude]
    kept.sort(key=lambda r: r.scientific_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([r.model_dump(exclude_none=True) for r in kept], indent=2, ensure_ascii=False)
        + "\n"
    )
    return len(kept)


def seed_scientific_names(seed_path: Path) -> set[str]:
    if not seed_path.exists():
        return set()
    return {
        r["scientific_name"] for r in json.loads(seed_path.read_text()) if r.get("scientific_name")
    }
