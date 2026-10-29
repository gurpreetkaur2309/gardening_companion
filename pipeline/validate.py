"""Validate: hard schema checks. Failures are kept, with the reason."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from .extract import Rejected
from .records import PlantRecord


def validate_all(rows: list[dict[str, Any]]) -> tuple[list[PlantRecord], list[Rejected]]:
    valid: list[PlantRecord] = []
    rejected: list[Rejected] = []
    for row in rows:
        try:
            valid.append(PlantRecord.model_validate(row))
        except ValidationError as exc:
            first = exc.errors()[0]
            field = ".".join(str(p) for p in first["loc"])
            rejected.append(
                Rejected(row.get("scientific_name") or "?", "validate", f"invalid_{field}")
            )
    return valid, rejected
