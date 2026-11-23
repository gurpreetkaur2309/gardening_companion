"""Record shapes for each stage, and JSONL helpers."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator


class RawRecord(BaseModel):
    """Everything extracted for one candidate, untouched apart from selection."""

    candidate: str  # the Wikipedia title we started from
    wiki: dict[str, Any]
    wikidata: dict[str, Any]  # taxon_name, rank_id, common_names
    gbif: dict[str, Any]


class CleanRecord(BaseModel):
    candidate: str
    scientific_name: str
    common_name: str
    family: str | None
    genus: str | None
    extract: str
    image_url: str | None
    wikipedia_url: str | None
    gbif_key: int | None
    wikidata_id: str | None


class PlantRecord(BaseModel):
    """The catalog shape: matches ``data/plants_seed.json`` plus provenance."""

    common_name: str = Field(min_length=2, max_length=80)
    scientific_name: str
    family: str = Field(min_length=3, max_length=60)
    genus: str = Field(min_length=3, max_length=60)
    edible: bool = False
    description: str = Field(min_length=60, max_length=600)
    image_key: str | None = None
    # Care is filled by the enrichment stage; absent until then.
    care: dict[str, Any] | None = None
    care_source: str = "default"
    provenance: dict[str, Any] = Field(default_factory=dict)

    @field_validator("scientific_name")
    @classmethod
    def _binomial(cls, v: str) -> str:
        parts = v.split()
        if len(parts) < 2 or not parts[0][:1].isupper() or not parts[1].islower():
            raise ValueError("scientific_name must look like 'Genus species'")
        return v

    @field_validator("image_key")
    @classmethod
    def _https(cls, v: str | None) -> str | None:
        if v is not None and not v.startswith("https://"):
            raise ValueError("image URL must be https")
        return v


def write_jsonl(path: Path, rows: Iterable[BaseModel | dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w") as fh:
        for row in rows:
            data = row.model_dump() if isinstance(row, BaseModel) else row
            fh.write(json.dumps(data, ensure_ascii=False, default=str) + "\n")
            n += 1
    return n


def read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open() as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)
