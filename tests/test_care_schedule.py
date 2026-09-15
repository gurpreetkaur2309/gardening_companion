"""The care-date arithmetic. This is the product's core, so it's tested hardest."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from bson import ObjectId

from app.models import AgeStage, CareAction, CareSchedule, GardenPlant
from app.models.plant import PlantCare


def make_planting(**kwargs) -> GardenPlant:
    defaults = {
        "_id": ObjectId(),
        "user_id": ObjectId(),
        "garden_id": ObjectId(),
        "plant_id": ObjectId(),
    }
    return GardenPlant(**{**defaults, **kwargs})


# --- Stage scaling ---------------------------------------------------------
@pytest.mark.parametrize(
    ("stage", "expected_water"),
    [(AgeStage.SEEDLING, 5), (AgeStage.YOUNG, 8), (AgeStage.MATURE, 10)],
)
def test_stage_scales_intervals(stage, expected_water):
    care = PlantCare(watering_days=10, fertilizing_days=40)
    schedule = CareSchedule.for_stage(care, stage)
    assert schedule.watering_days == expected_water


def test_schedule_clamps_absurd_values():
    """An LLM or a typo must never produce 'water every 0 days'."""
    assert CareSchedule(watering_days=0).watering_days == 1
    assert CareSchedule(watering_days=9999).watering_days == 120
    assert CareSchedule(fertilizing_days=1).fertilizing_days == 3
    assert CareSchedule(fertilizing_days=10_000).fertilizing_days == 365


def test_seedling_of_a_thirsty_plant_never_hits_zero():
    schedule = CareSchedule.for_stage(PlantCare(watering_days=1), AgeStage.SEEDLING)
    assert schedule.watering_days >= 1


# --- Due dates -------------------------------------------------------------
def test_due_date_counts_from_planting_when_never_watered():
    planted = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
    planting = make_planting(planted_at=planted, schedule=CareSchedule(watering_days=7))
    planting.refresh_due_dates()
    assert planting.next_water_due == planted + timedelta(days=7)


def test_due_date_counts_from_last_watering_once_it_exists():
    planted = datetime(2026, 3, 1, tzinfo=UTC)
    watered = datetime(2026, 3, 10, tzinfo=UTC)
    planting = make_planting(
        planted_at=planted, last_watered_at=watered, schedule=CareSchedule(watering_days=7)
    )
    planting.refresh_due_dates()
    assert planting.next_water_due == watered + timedelta(days=7)


def test_days_until_compares_calendar_days_not_elapsed_hours():
    """Due at 23:00 tonight is 'today' (0), not 'tomorrow' — a plant due this
    evening must not be reported as due in a day."""
    now = datetime(2026, 3, 10, 8, 0, tzinfo=UTC)
    planting = make_planting(next_water_due=datetime(2026, 3, 10, 23, 0, tzinfo=UTC))
    assert planting.days_until(planting.next_water_due, now=now) == 0


def test_naive_datetimes_are_treated_as_utc():
    """Documents written before tz_aware was enabled must not crash the page."""
    now = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
    planting = make_planting(next_water_due=datetime(2026, 3, 12, 12, 0))  # naive
    assert planting.days_until(planting.next_water_due, now=now) == 2


def test_overdue_is_negative():
    now = datetime(2026, 3, 10, tzinfo=UTC)
    planting = make_planting(next_water_due=datetime(2026, 3, 7, tzinfo=UTC))
    assert planting.days_until(planting.next_water_due, now=now) == -3
    assert planting.is_due(planting.next_water_due, now=now)


def test_urgency_reflects_the_soonest_of_the_two_tasks():
    now = datetime.now(UTC)
    planting = make_planting(
        next_water_due=now + timedelta(days=5),
        next_fertilize_due=now - timedelta(days=2),
    )
    assert planting.urgency == "overdue"


def test_muted_plant_is_never_urgent():
    planting = make_planting(
        next_water_due=datetime.now(UTC) - timedelta(days=30), reminders_enabled=False
    )
    assert planting.urgency == "upcoming"
    assert not planting.needs_water


# --- Recording care --------------------------------------------------------
def test_watering_advances_the_due_date_and_logs_it(app, user, overdue_planting):
    from app.repositories import care_log_repo
    from app.services import care_service

    with app.app_context():
        before = overdue_planting.next_water_due
        updated = care_service.record_care(
            overdue_planting.id, user.id, CareAction.WATER, note="bone dry"
        )

        assert updated.next_water_due > before
        assert updated.next_water_due > datetime.now(UTC)

        history = care_log_repo.list_for_planting(overdue_planting.id)
        assert len(history) == 1
        assert history[0].action == CareAction.WATER
        assert history[0].note == "bone dry"
        # It was three days overdue, so this counts as late.
        assert history[0].was_late is True


def test_watering_on_time_is_not_flagged_late(app, user, planting):
    from app.repositories import care_log_repo
    from app.services import care_service

    with app.app_context():
        care_service.record_care(planting.id, user.id, CareAction.WATER)
        assert care_log_repo.list_for_planting(planting.id)[0].was_late is False


def test_fertilizing_does_not_touch_the_watering_date(app, user, planting):
    from app.repositories import garden_plant_repo
    from app.services import care_service

    with app.app_context():
        before = garden_plant_repo.get(planting.id, user.id).next_water_due
        care_service.record_care(planting.id, user.id, CareAction.FERTILIZE)
        after = garden_plant_repo.get(planting.id, user.id)
        assert after.next_water_due == before
        assert after.next_fertilize_due > datetime.now(UTC)


def test_repotting_brings_watering_forward(app, user, planting):
    """Disturbed roots need water sooner than the normal interval."""
    from app.repositories import garden_plant_repo
    from app.services import care_service

    with app.app_context():
        before = garden_plant_repo.get(planting.id, user.id).next_water_due
        care_service.record_care(planting.id, user.id, CareAction.REPOT)
        after = garden_plant_repo.get(planting.id, user.id).next_water_due
        assert after < before


def test_unknown_action_is_rejected(app, user, planting):
    from app.errors import ValidationError
    from app.services import care_service

    with app.app_context(), pytest.raises(ValidationError):
        care_service.record_care(planting.id, user.id, "photosynthesise")


# --- Snooze ----------------------------------------------------------------
def test_snooze_from_now_not_from_a_stale_due_date(app, user, overdue_planting):
    """An overdue plant snoozed for 2 days should be quiet for a full 2 days."""
    from app.services import care_service

    with app.app_context():
        updated = care_service.snooze(overdue_planting.id, user.id, CareAction.WATER, days=2)
        assert updated.days_until_water == 2


def test_snooze_rejects_silly_durations(app, user, planting):
    from app.errors import ValidationError
    from app.services import care_service

    with app.app_context():
        with pytest.raises(ValidationError):
            care_service.snooze(planting.id, user.id, CareAction.WATER, days=0)
        with pytest.raises(ValidationError):
            care_service.snooze(planting.id, user.id, CareAction.WATER, days=365)


# --- Grouping --------------------------------------------------------------
def test_group_by_urgency_sorts_worst_first(app):
    from app.services import care_service

    now = datetime.now(UTC)
    plantings = [
        make_planting(next_water_due=now - timedelta(days=1)),
        make_planting(next_water_due=now - timedelta(days=9)),
        make_planting(next_water_due=now),
        make_planting(next_water_due=now + timedelta(days=4)),
    ]
    buckets = care_service.group_by_urgency(plantings, now=now)

    assert len(buckets.overdue) == 2
    assert len(buckets.due_today) == 1
    assert len(buckets.upcoming) == 1
    # The most overdue plant leads the list.
    assert buckets.overdue[0].days_until_water == -9
    assert buckets.needs_attention == 3
