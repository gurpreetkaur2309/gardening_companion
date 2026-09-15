"""Care scheduling: resolving schedules, recording actions, grouping by urgency.

This is the heart of the product and deliberately has no Flask imports beyond
the repositories it calls, so the date arithmetic can be unit-tested directly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from bson import ObjectId

from ..errors import NotFoundError, ValidationError
from ..models import (
    AgeStage,
    CareAction,
    CareLog,
    CareSchedule,
    GardenPlant,
    Plant,
)
from ..models.enums import CareSource
from ..repositories import (
    care_log_repo,
    garden_plant_repo,
    garden_repo,
    plant_repo,
)
from . import gemini_service

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schedule resolution
# ---------------------------------------------------------------------------
def resolve_schedule(plant: Plant, stage: AgeStage) -> tuple[CareSchedule, str]:
    """Work out the effective schedule for a new planting.

    Order of preference: curated catalog data, then a one-off Gemini lookup
    (cached onto the species so it never repeats), then a conservative default.
    Returns the schedule and the source it came from.
    """
    if plant.has_care_data:
        return CareSchedule.for_stage(plant.care, stage), plant.care_source

    result = gemini_service.infer_care_schedule(plant.common_name, plant.scientific_name)
    if result.succeeded:
        # Cache on the species document: the next user to add a Monstera pays
        # nothing. This is the single biggest cost lever in the app.
        try:
            plant_repo.cache_care(plant.id, result.care, result.source)
        except Exception:
            logger.warning("Could not cache care data for %s", plant.id, exc_info=True)

    return CareSchedule.for_stage(result.care, stage), result.source


def apply_stage_change(planting: GardenPlant, new_stage: AgeStage) -> CareSchedule:
    """Rescale a planting's schedule when its life stage changes.

    The stored schedule is already stage-scaled, so it is un-scaled back to the
    species baseline before the new multiplier is applied.
    """
    old_factor = planting.age_stage_enum.interval_multiplier
    baseline_water = planting.schedule.watering_days / old_factor
    baseline_fert = planting.schedule.fertilizing_days / old_factor
    factor = new_stage.interval_multiplier
    return CareSchedule(
        watering_days=round(baseline_water * factor),
        fertilizing_days=round(baseline_fert * factor),
        sunlight=planting.schedule.sunlight,
        fertilizer_type=planting.schedule.fertilizer_type,
    )


# ---------------------------------------------------------------------------
# Recording care
# ---------------------------------------------------------------------------
ACTION_FIELDS: dict[str, tuple[str, str]] = {
    CareAction.WATER: ("last_watered_at", "next_water_due"),
    CareAction.FERTILIZE: ("last_fertilized_at", "next_fertilize_due"),
}


def record_care(
    planting_id: ObjectId,
    user_id: ObjectId,
    action: str,
    *,
    note: str | None = None,
    when: datetime | None = None,
) -> GardenPlant:
    """Log a care action and roll the relevant due date forward.

    Raises ``NotFoundError`` if the planting isn't the caller's — a missing row
    and someone else's row are indistinguishable from outside, on purpose.
    """
    try:
        action_enum = CareAction(action)
    except ValueError as exc:
        raise ValidationError(f"Unknown care action: {action}") from exc

    planting = garden_plant_repo.get(planting_id, user_id)
    if planting is None:
        raise NotFoundError("No such plant in your garden.")

    moment = when or datetime.now(UTC)

    # "Late" is judged against the due date this action is settling, before the
    # date moves — otherwise every action would look on time.
    was_late = False
    updates: dict[str, Any] = {}
    if action_enum in ACTION_FIELDS:
        last_field, next_field = ACTION_FIELDS[action_enum]
        previous_due = getattr(planting, next_field)
        if previous_due is not None:
            due = previous_due if previous_due.tzinfo else previous_due.replace(tzinfo=UTC)
            was_late = moment.date() > due.date()

        setattr(planting, last_field, moment)
        interval = (
            planting.schedule.watering_days
            if action_enum is CareAction.WATER
            else planting.schedule.fertilizing_days
        )
        new_due = moment + timedelta(days=interval)
        setattr(planting, next_field, new_due)
        updates[last_field] = moment
        updates[next_field] = new_due

    if action_enum is CareAction.REPOT:
        # Repotting disturbs roots; the plant wants water sooner than usual.
        planting.next_water_due = moment + timedelta(
            days=max(1, planting.schedule.watering_days // 2)
        )
        updates["next_water_due"] = planting.next_water_due

    if updates:
        garden_plant_repo.update_fields(planting_id, user_id, updates)

    care_log_repo.create(
        CareLog(
            _id=ObjectId(),
            garden_plant_id=planting_id,
            user_id=user_id,
            action=str(action_enum),
            note=(note or "").strip() or None,
            was_late=was_late,
            created_at=moment,
        )
    )
    return planting


def snooze(planting_id: ObjectId, user_id: ObjectId, action: str, days: int = 1) -> GardenPlant:
    """Push a due date back without claiming the task was done."""
    if action not in ACTION_FIELDS:
        raise ValidationError("Only watering and fertilizing can be snoozed.")
    if not 1 <= days <= 30:
        raise ValidationError("Snooze must be between 1 and 30 days.")

    planting = garden_plant_repo.get(planting_id, user_id)
    if planting is None:
        raise NotFoundError("No such plant in your garden.")

    _, next_field = ACTION_FIELDS[action]
    current = getattr(planting, next_field) or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    # Snoozing an already-overdue task from "now", not from the stale due date,
    # so one snooze always buys a full `days` of quiet.
    base = max(current, datetime.now(UTC))
    new_due = base + timedelta(days=days)
    garden_plant_repo.update_fields(planting_id, user_id, {next_field: new_due})
    setattr(planting, next_field, new_due)
    return planting


# ---------------------------------------------------------------------------
# Dashboard assembly
# ---------------------------------------------------------------------------
@dataclass
class CareBuckets:
    """Plantings grouped by how urgently they need attention."""

    overdue: list[GardenPlant] = field(default_factory=list)
    due_today: list[GardenPlant] = field(default_factory=list)
    upcoming: list[GardenPlant] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.overdue) + len(self.due_today) + len(self.upcoming)

    @property
    def needs_attention(self) -> int:
        return len(self.overdue) + len(self.due_today)

    @property
    def all_plantings(self) -> list[GardenPlant]:
        return [*self.overdue, *self.due_today, *self.upcoming]


def hydrate(plantings: list[GardenPlant]) -> list[GardenPlant]:
    """Attach the catalog species to each planting in one batched query."""
    if not plantings:
        return plantings
    species = plant_repo.get_many([p.plant_id for p in plantings])
    for planting in plantings:
        planting.plant = species.get(planting.plant_id)
    return plantings


def group_by_urgency(
    plantings: list[GardenPlant], *, now: datetime | None = None
) -> CareBuckets:
    """Split plantings into overdue / due today / upcoming.

    Within each bucket the soonest task sorts first, so the top of the page is
    always the thing most worth doing.
    """
    moment = now or datetime.now(UTC)
    buckets = CareBuckets()

    for planting in plantings:
        days = [
            d
            for d in (
                planting.days_until(planting.next_water_due, now=moment),
                planting.days_until(planting.next_fertilize_due, now=moment),
            )
            if d is not None
        ]
        if not planting.reminders_enabled or not days:
            buckets.upcoming.append(planting)
            continue
        soonest = min(days)
        if soonest < 0:
            buckets.overdue.append(planting)
        elif soonest == 0:
            buckets.due_today.append(planting)
        else:
            buckets.upcoming.append(planting)

    def sort_key(p: GardenPlant) -> int:
        days = [
            d
            for d in (
                p.days_until(p.next_water_due, now=moment),
                p.days_until(p.next_fertilize_due, now=moment),
            )
            if d is not None
        ]
        return min(days) if days else 9999

    buckets.overdue.sort(key=sort_key)
    buckets.due_today.sort(key=sort_key)
    buckets.upcoming.sort(key=sort_key)
    return buckets


def pending_actions(planting: GardenPlant, *, now: datetime | None = None) -> list[CareAction]:
    """Which scheduled actions are currently due for this planting."""
    moment = now or datetime.now(UTC)
    if not planting.reminders_enabled:
        return []
    actions = []
    if planting.is_due(planting.next_water_due, now=moment):
        actions.append(CareAction.WATER)
    if planting.is_due(planting.next_fertilize_due, now=moment):
        actions.append(CareAction.FERTILIZE)
    return actions


def upcoming_week(user_id: ObjectId, *, now: datetime | None = None) -> list[dict[str, Any]]:
    """A seven-day outlook for the dashboard timeline strip."""
    moment = now or datetime.now(UTC)
    plantings = hydrate(garden_plant_repo.list_for_user(user_id))

    days: list[dict[str, Any]] = []
    for offset in range(7):
        day = (moment + timedelta(days=offset)).date()
        water = []
        fertilize = []
        for planting in plantings:
            if not planting.reminders_enabled:
                continue
            due_w = planting.next_water_due
            due_f = planting.next_fertilize_due
            # Everything already overdue is folded into today's column rather
            # than disappearing off the left edge of the strip.
            if due_w and (due_w.date() == day or (offset == 0 and due_w.date() < day)):
                water.append(planting)
            if due_f and (due_f.date() == day or (offset == 0 and due_f.date() < day)):
                fertilize.append(planting)
        days.append(
            {
                "date": day,
                "is_today": offset == 0,
                "water": water,
                "fertilize": fertilize,
                "total": len(water) + len(fertilize),
            }
        )
    return days


def user_summary(user_id: ObjectId) -> dict[str, Any]:
    """Headline numbers for the dashboard stat row."""
    stats = garden_plant_repo.user_stats(user_id)
    return {
        **stats,
        "gardens": garden_repo.count_for_user(user_id),
        "streak": care_log_repo.active_day_streak(user_id),
        "on_time_rate": care_log_repo.on_time_rate(user_id),
        "actions_30d": sum(care_log_repo.action_counts(user_id, days=30).values()),
    }


def build_chat_context(user_id: ObjectId, display_name: str) -> str:
    """Render the user's real garden for the chatbot's system prompt."""
    gardens = garden_repo.list_for_user(user_id)
    garden_names = {g.id: g.name for g in gardens}
    plantings = hydrate(garden_plant_repo.list_for_user(user_id))
    buckets = group_by_urgency(plantings)

    return gemini_service.build_garden_context(
        display_name=display_name,
        gardens=[g.name for g in gardens],
        plants=[
            {
                "name": p.display_name,
                "species": p.plant.common_name if p.plant else None,
                "quantity": p.quantity,
                "age_stage": str(p.age_stage),
                "watering_days": p.schedule.watering_days,
                "garden": garden_names.get(p.garden_id),
            }
            for p in plantings
        ],
        overdue=[p.display_name for p in buckets.overdue],
        due_today=[p.display_name for p in buckets.due_today],
    )


__all__ = [
    "CareBuckets",
    "CareSource",
    "apply_stage_change",
    "build_chat_context",
    "group_by_urgency",
    "hydrate",
    "pending_actions",
    "record_care",
    "resolve_schedule",
    "snooze",
    "upcoming_week",
    "user_summary",
]
