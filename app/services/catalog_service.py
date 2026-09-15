"""The shared plant catalog and the request-a-plant queue."""

from __future__ import annotations

import logging
from typing import Any

from bson import ObjectId

from ..errors import ConflictError, NotFoundError, ValidationError
from ..models import Plant, PlantRequest, RequestStatus
from ..models.enums import CareSource, Sunlight
from ..models.plant import PlantCare
from ..repositories import garden_plant_repo, plant_repo, plant_request_repo, user_repo
from . import storage_service

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------
def get_plant(plant_id: ObjectId) -> Plant:
    plant = plant_repo.get(plant_id)
    if plant is None:
        raise NotFoundError("That plant isn't in the catalog.")
    return plant


def search(query: str | None = None, *, limit: int = 500) -> list[Plant]:
    return plant_repo.list_all(search=query, limit=limit)


def _clean_care(fields: dict[str, Any]) -> PlantCare | None:
    """Build a PlantCare from admin form input, if any care fields were given."""
    keys = ("watering_days", "fertilizing_days", "sunlight", "fertilizer_type")
    if not any(fields.get(k) not in (None, "") for k in keys):
        return None

    sunlight = (fields.get("sunlight") or Sunlight.PARTIAL).strip().lower()
    if sunlight not in set(Sunlight):
        sunlight = Sunlight.PARTIAL

    def as_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    return PlantCare(
        watering_days=max(1, min(as_int(fields.get("watering_days"), 7), 120)),
        fertilizing_days=max(3, min(as_int(fields.get("fertilizing_days"), 30), 365)),
        sunlight=sunlight,
        fertilizer_type=(fields.get("fertilizer_type") or "Balanced 10-10-10").strip()[:80],
        notes=(fields.get("care_notes") or "").strip()[:400] or None,
    )


def create_plant(fields: dict[str, Any], *, image=None) -> Plant:
    """Add a species to the catalog. Admin-only at the blueprint layer."""
    common_name = (fields.get("common_name") or "").strip()
    if not common_name:
        raise ValidationError("A common name is required.")
    if len(common_name) > 120:
        raise ValidationError("Common names are limited to 120 characters.")

    scientific_name = (fields.get("scientific_name") or "").strip() or None
    if scientific_name and plant_repo.collection.find_one({"scientific_name": scientific_name}):
        raise ConflictError(f"'{scientific_name}' is already in the catalog.")
    if not scientific_name and plant_repo.get_by_common_name(common_name):
        raise ConflictError(f"'{common_name}' is already in the catalog.")

    care = _clean_care(fields)
    image_key = storage_service.store_upload(image) if image else None

    plant = Plant(
        _id=ObjectId(),
        common_name=common_name,
        scientific_name=scientific_name,
        family=(fields.get("family") or "").strip() or None,
        genus=(fields.get("genus") or "").strip() or None,
        rank=(fields.get("rank") or "").strip() or None,
        edible=bool(fields.get("edible")),
        description=(fields.get("description") or "").strip()[:4000] or None,
        sapling_description=(fields.get("sapling_description") or "").strip()[:4000] or None,
        image_key=image_key,
        care=care or PlantCare(),
        care_source=CareSource.MANUAL if care else CareSource.DEFAULT,
    )
    plant_repo.create(plant)
    logger.info("Catalog: added %s", plant.common_name)
    return plant


def update_plant(plant_id: ObjectId, fields: dict[str, Any], *, image=None) -> Plant:
    plant = get_plant(plant_id)
    updates: dict[str, Any] = {}

    for key, limit in (
        ("common_name", 120),
        ("scientific_name", 160),
        ("family", 120),
        ("genus", 120),
        ("rank", 60),
    ):
        if key in fields:
            value = (fields[key] or "").strip()[:limit]
            updates[key] = value or None
    if "common_name" in updates and not updates["common_name"]:
        raise ValidationError("A common name is required.")

    for key in ("description", "sapling_description"):
        if key in fields:
            updates[key] = (fields[key] or "").strip()[:4000] or None

    if "edible" in fields:
        updates["edible"] = bool(fields["edible"])

    care = _clean_care(fields)
    if care:
        updates["care"] = care.to_doc()
        updates["care_source"] = str(CareSource.MANUAL)

    if image:
        new_key = storage_service.store_upload(image)
        old_key = plant.image_key
        updates["image_key"] = new_key
        if old_key and old_key != new_key:
            storage_service.delete_image(old_key)

    if updates:
        plant_repo.update_fields(plant_id, updates)
    return get_plant(plant_id)


def delete_plant(plant_id: ObjectId, *, force: bool = False) -> None:
    """Remove a species from the catalog.

    Refuses while anyone still grows it — deleting would leave orphaned cards on
    their dashboards.  The old code deleted unconditionally.
    """
    plant = get_plant(plant_id)
    in_use = garden_plant_repo.count_using_plant(plant_id)
    if in_use and not force:
        raise ConflictError(
            f"{in_use} planting(s) still reference '{plant.common_name}'. "
            "Remove them first, or force the delete."
        )
    if plant.image_key:
        storage_service.delete_image(plant.image_key)
    plant_repo.delete(plant_id)
    logger.info("Catalog: deleted %s (in_use=%s, force=%s)", plant.common_name, in_use, force)


# ---------------------------------------------------------------------------
# Plant requests
# ---------------------------------------------------------------------------
def submit_request(user_id: ObjectId, plant_name: str, description: str | None) -> PlantRequest:
    name = (plant_name or "").strip()
    if not name:
        raise ValidationError("Tell us which plant you'd like added.")
    if len(name) > 120:
        raise ValidationError("That name is too long.")

    if plant_repo.get_by_common_name(name):
        raise ConflictError(f"'{name}' is already in the catalog — search for it when adding.")
    if plant_request_repo.has_pending_for_name(name):
        raise ConflictError(f"'{name}' has already been requested and is awaiting review.")

    request = PlantRequest(
        _id=ObjectId(),
        user_id=user_id,
        plant_name=name,
        description=(description or "").strip()[:2000] or None,
        status=RequestStatus.PENDING,
    )
    plant_request_repo.create(request)
    logger.info("Plant request submitted: %s", name)
    return request


def list_requests(status: str = RequestStatus.PENDING) -> list[PlantRequest]:
    """Pending requests with their requester attached, for the admin queue."""
    requests = plant_request_repo.list_by_status(status)
    for request in requests:
        request.requester = user_repo.get(request.user_id)
    return requests


def approve_request(
    request_id: ObjectId, reviewer_id: ObjectId, fields: dict[str, Any], *, image=None
) -> tuple[PlantRequest, Plant]:
    """Approve a request by creating the catalog entry it asked for."""
    request = plant_request_repo.get(request_id)
    if request is None:
        raise NotFoundError("No such plant request.")
    if not request.is_pending:
        raise ConflictError("That request has already been reviewed.")

    payload = {"common_name": request.plant_name, **fields}
    plant = create_plant(payload, image=image)

    plant_request_repo.mark_reviewed(
        request_id,
        status=RequestStatus.APPROVED,
        reviewer_id=reviewer_id,
        note=(fields.get("review_note") or "").strip() or None,
        created_plant_id=plant.id,
    )
    refreshed = plant_request_repo.get(request_id)
    return (refreshed or request), plant


def reject_request(
    request_id: ObjectId, reviewer_id: ObjectId, note: str | None = None
) -> PlantRequest:
    request = plant_request_repo.get(request_id)
    if request is None:
        raise NotFoundError("No such plant request.")
    if not request.is_pending:
        raise ConflictError("That request has already been reviewed.")

    plant_request_repo.mark_reviewed(
        request_id,
        status=RequestStatus.REJECTED,
        reviewer_id=reviewer_id,
        note=(note or "").strip() or None,
    )
    refreshed = plant_request_repo.get(request_id)
    return refreshed or request
