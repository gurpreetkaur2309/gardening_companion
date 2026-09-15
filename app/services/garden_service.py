"""Garden and planting operations.

Every function takes the acting ``user_id`` and scopes its queries by it.  There
is no code path that reaches a garden or planting without proving ownership
first — the previous version trusted ids straight out of the URL.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from bson import ObjectId

from ..errors import ConflictError, NotFoundError, ValidationError
from ..models import AgeStage, Garden, GardenPlant, Plant
from ..repositories import (
    care_log_repo,
    garden_plant_repo,
    garden_repo,
    plant_repo,
    reminder_repo,
)
from . import care_service

logger = logging.getLogger(__name__)

DEFAULT_GARDEN_NAME = "My Garden"
MAX_GARDENS_PER_USER = 25
MAX_QUANTITY = 999


# ---------------------------------------------------------------------------
# Gardens
# ---------------------------------------------------------------------------
def ensure_default_garden(user_id: ObjectId) -> Garden:
    """Every user always has at least one garden, created on first use."""
    existing = garden_repo.first_for_user(user_id)
    if existing:
        return existing
    garden = Garden(
        _id=ObjectId(),
        user_id=user_id,
        name=DEFAULT_GARDEN_NAME,
        description="Your first garden. Rename it or add more from the dashboard.",
    )
    try:
        return garden_repo.create(garden)
    except ConflictError:
        # Another request created it between our read and write.
        found = garden_repo.first_for_user(user_id)
        if found:
            return found
        raise


def list_gardens(user_id: ObjectId) -> list[Garden]:
    return garden_repo.list_for_user(user_id)


def get_garden(garden_id: ObjectId, user_id: ObjectId) -> Garden:
    garden = garden_repo.get(garden_id, user_id)
    if garden is None:
        raise NotFoundError("No such garden.")
    return garden


def resolve_active_garden(
    user_id: ObjectId, requested_id: str | None
) -> tuple[Garden, list[Garden]]:
    """Pick the garden to display, tolerating a stale or bogus id in the URL.

    Returns the active garden plus the full list for the switcher.
    """
    gardens = garden_repo.list_for_user(user_id)
    if not gardens:
        default = ensure_default_garden(user_id)
        return default, [default]

    if requested_id:
        try:
            wanted = ObjectId(requested_id)
        except Exception:
            wanted = None
        if wanted is not None:
            for garden in gardens:
                if garden.id == wanted:
                    return garden, gardens
    return gardens[0], gardens


def create_garden(
    user_id: ObjectId, name: str, *, location: str | None = None, description: str | None = None
) -> Garden:
    clean = (name or "").strip()
    if not clean:
        raise ValidationError("Give your garden a name.")
    if len(clean) > 60:
        raise ValidationError("Garden names are limited to 60 characters.")
    if garden_repo.count_for_user(user_id) >= MAX_GARDENS_PER_USER:
        raise ValidationError(f"You can have at most {MAX_GARDENS_PER_USER} gardens.")

    return garden_repo.create(
        Garden(
            _id=ObjectId(),
            user_id=user_id,
            name=clean,
            location=(location or "").strip() or None,
            description=(description or "").strip() or None,
        )
    )


def update_garden(garden_id: ObjectId, user_id: ObjectId, fields: dict[str, Any]) -> Garden:
    get_garden(garden_id, user_id)  # ownership check
    clean: dict[str, Any] = {}
    if "name" in fields:
        name = (fields["name"] or "").strip()
        if not name:
            raise ValidationError("Give your garden a name.")
        clean["name"] = name[:60]
    for key in ("location", "description"):
        if key in fields:
            clean[key] = (fields[key] or "").strip() or None
    if clean:
        garden_repo.update_fields(garden_id, user_id, clean)
    return get_garden(garden_id, user_id)


def delete_garden(garden_id: ObjectId, user_id: ObjectId) -> int:
    """Delete a garden and everything planted in it. Returns plantings removed.

    The last garden can't be deleted — the dashboard assumes one exists, and a
    user with zero gardens has nowhere to put a plant.
    """
    get_garden(garden_id, user_id)
    if garden_repo.count_for_user(user_id) <= 1:
        raise ValidationError(
            "This is your only garden. Create another one before deleting this."
        )

    plantings = garden_plant_repo.list_for_garden(garden_id, user_id)
    for planting in plantings:
        care_log_repo.delete_for_planting(planting.id)
        reminder_repo.delete_for_planting(planting.id)
    removed = garden_plant_repo.delete_for_garden(garden_id, user_id)
    garden_repo.delete(garden_id, user_id)
    return removed


# ---------------------------------------------------------------------------
# Plantings
# ---------------------------------------------------------------------------
def add_plant(
    *,
    user_id: ObjectId,
    garden_id: ObjectId,
    plant_id: ObjectId,
    age_stage: str = AgeStage.MATURE,
    quantity: int = 1,
    nickname: str | None = None,
    planted_at: datetime | None = None,
) -> tuple[GardenPlant, bool]:
    """Add a species to a garden, or increase the count if it's already there.

    Returns ``(planting, created)``.  Merging on (species, life stage) keeps the
    dashboard from filling with duplicate cards, while still letting a user track
    a seedling and a mature specimen of the same species separately.
    """
    get_garden(garden_id, user_id)

    plant: Plant | None = plant_repo.get(plant_id)
    if plant is None:
        raise NotFoundError("That plant isn't in the catalog.")

    try:
        stage = AgeStage(age_stage)
    except ValueError:
        stage = AgeStage.MATURE

    quantity = max(1, min(int(quantity or 1), MAX_QUANTITY))

    existing = garden_plant_repo.find_existing(
        user_id=user_id, garden_id=garden_id, plant_id=plant_id, age_stage=str(stage)
    )
    if existing and not nickname:
        new_total = min(existing.quantity + quantity, MAX_QUANTITY)
        garden_plant_repo.update_fields(existing.id, user_id, {"quantity": new_total})
        existing.quantity = new_total
        existing.plant = plant
        return existing, False

    schedule, source = care_service.resolve_schedule(plant, stage)
    when = planted_at or datetime.now(UTC)

    planting = GardenPlant(
        _id=ObjectId(),
        user_id=user_id,
        garden_id=garden_id,
        plant_id=plant_id,
        nickname=(nickname or "").strip()[:60] or None,
        age_stage=str(stage),
        quantity=quantity,
        planted_at=when,
        schedule=schedule,
        reminders_enabled=True,
    )
    planting.refresh_due_dates()
    garden_plant_repo.create(planting)
    planting.plant = plant
    logger.info(
        "Planted %s (schedule from %s) in garden %s", plant.common_name, source, garden_id
    )
    return planting, True


def get_planting(planting_id: ObjectId, user_id: ObjectId) -> GardenPlant:
    planting = garden_plant_repo.get(planting_id, user_id)
    if planting is None:
        raise NotFoundError("No such plant in your garden.")
    planting.plant = plant_repo.get(planting.plant_id)
    return planting


def list_plantings(garden_id: ObjectId, user_id: ObjectId) -> list[GardenPlant]:
    return care_service.hydrate(garden_plant_repo.list_for_garden(garden_id, user_id))


def update_planting(
    planting_id: ObjectId, user_id: ObjectId, fields: dict[str, Any]
) -> GardenPlant:
    planting = get_planting(planting_id, user_id)
    updates: dict[str, Any] = {}

    if "nickname" in fields:
        updates["nickname"] = (fields["nickname"] or "").strip()[:60] or None

    if "notes" in fields:
        updates["notes"] = (fields["notes"] or "").strip()[:2000] or None

    if "quantity" in fields:
        quantity = max(1, min(int(fields["quantity"] or 1), MAX_QUANTITY))
        updates["quantity"] = quantity

    if "reminders_enabled" in fields:
        updates["reminders_enabled"] = bool(fields["reminders_enabled"])

    if "age_stage" in fields and fields["age_stage"] != planting.age_stage:
        try:
            stage = AgeStage(fields["age_stage"])
        except ValueError as exc:
            raise ValidationError("Unknown life stage.") from exc
        schedule = care_service.apply_stage_change(planting, stage)
        updates["age_stage"] = str(stage)
        updates["schedule"] = schedule.to_doc()
        planting.schedule = schedule
        planting.refresh_due_dates()
        updates["next_water_due"] = planting.next_water_due
        updates["next_fertilize_due"] = planting.next_fertilize_due

    # A manual interval override wins over anything the catalog or AI supplied.
    schedule_changed = False
    schedule = planting.schedule
    for key, attr in (
        ("watering_days", "watering_days"),
        ("fertilizing_days", "fertilizing_days"),
        ("fertilizer_type", "fertilizer_type"),
        ("sunlight", "sunlight"),
    ):
        if key in fields and fields[key] not in (None, ""):
            setattr(schedule, attr, fields[key])
            schedule_changed = True
    if schedule_changed:
        schedule = type(schedule)(**{**schedule.to_doc()})  # re-run clamping
        planting.schedule = schedule
        planting.refresh_due_dates()
        updates["schedule"] = schedule.to_doc()
        updates["next_water_due"] = planting.next_water_due
        updates["next_fertilize_due"] = planting.next_fertilize_due

    if updates:
        garden_plant_repo.update_fields(planting_id, user_id, updates)
    return get_planting(planting_id, user_id)


def remove_plant(planting_id: ObjectId, user_id: ObjectId, *, all_of_them: bool = False):
    """Remove one specimen, or the whole planting.

    Returns the remaining quantity, or ``None`` when the planting is gone.
    """
    planting = garden_plant_repo.get(planting_id, user_id)
    if planting is None:
        raise NotFoundError("No such plant in your garden.")

    if all_of_them or planting.quantity <= 1:
        care_log_repo.delete_for_planting(planting_id)
        reminder_repo.delete_for_planting(planting_id)
        garden_plant_repo.delete(planting_id, user_id)
        return None

    return garden_plant_repo.adjust_quantity(planting_id, user_id, -1)


def move_planting(
    planting_id: ObjectId, user_id: ObjectId, target_garden_id: ObjectId
) -> GardenPlant:
    get_garden(target_garden_id, user_id)
    planting = get_planting(planting_id, user_id)
    if planting.garden_id == target_garden_id:
        return planting
    garden_plant_repo.update_fields(planting_id, user_id, {"garden_id": target_garden_id})
    return get_planting(planting_id, user_id)


def purge_user_data(user_id: ObjectId) -> dict[str, int]:
    """Delete everything belonging to a user. Used when an account is removed."""
    plantings = garden_plant_repo.list_for_user(user_id)
    for planting in plantings:
        reminder_repo.delete_for_planting(planting.id)
    return {
        "plantings": garden_plant_repo.delete_all_for_user(user_id),
        "gardens": garden_repo.delete_all_for_user(user_id),
        "care_logs": care_log_repo.delete_all_for_user(user_id),
    }
