"""The reminder sweep, and the idempotency that stops duplicate emails."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest


def test_nothing_due_means_no_work(app, user, planting):
    from app.tasks.reminders import collect_due_tasks

    with app.app_context():
        assert collect_due_tasks() == {}


def test_an_overdue_plant_produces_a_task(app, user, overdue_planting):
    from app.tasks.reminders import collect_due_tasks

    with app.app_context():
        by_user = collect_due_tasks()

    assert user.id in by_user
    water = [t for t in by_user[user.id] if t["action"] == "water"]
    assert len(water) == 1
    assert water[0]["days_overdue"] == 3


def test_muted_plants_are_skipped(app, user, overdue_planting):
    from app.repositories import garden_plant_repo
    from app.tasks.reminders import collect_due_tasks

    with app.app_context():
        garden_plant_repo.update_fields(
            overdue_planting.id, user.id, {"reminders_enabled": False}
        )
        assert collect_due_tasks() == {}


def test_tasks_from_several_plants_group_into_one_users_digest(app, user, garden, make_plant):
    """Twenty plants due at once must mean one email, not twenty."""
    from app.repositories import garden_plant_repo
    from app.services import garden_service
    from app.tasks.reminders import collect_due_tasks

    past = datetime.now(UTC) - timedelta(days=2)
    with app.app_context():
        for i in range(5):
            species = make_plant(common_name=f"Species {i}")
            created, _ = garden_service.add_plant(
                user_id=user.id, garden_id=garden.id, plant_id=species.id
            )
            garden_plant_repo.update_fields(
                created.id, user.id, {"next_water_due": past, "next_fertilize_due": past}
            )

        by_user = collect_due_tasks()

    assert list(by_user.keys()) == [user.id]
    # Five plants, two due actions each.
    assert len(by_user[user.id]) == 10


# --- Idempotency -----------------------------------------------------------
def test_a_reminder_can_only_be_claimed_once(app, overdue_planting):
    from app.repositories import reminder_repo

    with app.app_context():
        due = date(2026, 3, 10)
        assert reminder_repo.claim(
            garden_plant_id=overdue_planting.id, action="water", due_on=due
        ) is True
        # A retried task, a second beat process, an overlapping sweep — all lose.
        assert reminder_repo.claim(
            garden_plant_id=overdue_planting.id, action="water", due_on=due
        ) is False


def test_claims_are_scoped_to_action_and_date(app, overdue_planting):
    from app.repositories import reminder_repo

    with app.app_context():
        reminder_repo.claim(
            garden_plant_id=overdue_planting.id, action="water", due_on=date(2026, 3, 10)
        )
        # Different action, same day: still allowed.
        assert reminder_repo.claim(
            garden_plant_id=overdue_planting.id, action="fertilize", due_on=date(2026, 3, 10)
        ) is True
        # Same action, next due date: allowed.
        assert reminder_repo.claim(
            garden_plant_id=overdue_planting.id, action="water", due_on=date(2026, 3, 17)
        ) is True


def test_releasing_a_claim_lets_the_next_sweep_retry(app, overdue_planting):
    """A failed SMTP send must not silently swallow the reminder."""
    from app.repositories import reminder_repo

    with app.app_context():
        due = date(2026, 3, 10)
        reminder_repo.claim(garden_plant_id=overdue_planting.id, action="water", due_on=due)
        assert reminder_repo.release(
            garden_plant_id=overdue_planting.id, action="water", due_on=due
        )
        assert reminder_repo.claim(
            garden_plant_id=overdue_planting.id, action="water", due_on=due
        ) is True


def test_sweep_sends_once_and_then_stays_quiet(app, user, overdue_planting, monkeypatch):
    from app.services import email_service
    from app.tasks import reminders

    sent = []
    monkeypatch.setattr(
        email_service, "send_care_digest",
        lambda u, tasks: (sent.append((u.email, len(tasks))) or True),
    )
    monkeypatch.setattr(
        reminders.email_service, "send_care_digest",
        lambda u, tasks: (sent.append((u.email, len(tasks))) or True),
    )

    with app.app_context():
        first = reminders.sweep_care_reminders(dry_run=False)
        second = reminders.sweep_care_reminders(dry_run=False)

    assert first["emails_sent"] == 1
    assert second["emails_sent"] == 0
    assert second["skipped_duplicates"] > 0
    assert len(sent) == 1


def test_a_failed_send_is_retried_by_the_next_sweep(app, user, overdue_planting, monkeypatch):
    from app.tasks import reminders

    attempts = {"n": 0}

    def flaky(_user, _tasks):
        attempts["n"] += 1
        return attempts["n"] > 1  # fail once, then succeed

    monkeypatch.setattr(reminders.email_service, "send_care_digest", flaky)

    with app.app_context():
        first = reminders.sweep_care_reminders(dry_run=False)
        second = reminders.sweep_care_reminders(dry_run=False)

    assert first["failed"] == 1
    assert first["emails_sent"] == 0
    assert second["emails_sent"] == 1


def test_dry_run_sends_nothing_and_claims_nothing(app, user, overdue_planting, monkeypatch):
    from app.repositories import reminder_repo
    from app.tasks import reminders

    monkeypatch.setattr(
        reminders.email_service, "send_care_digest",
        lambda *a, **k: pytest.fail("dry run must not send"),
    )

    with app.app_context():
        stats = reminders.sweep_care_reminders(dry_run=True)
        assert stats["tasks"] > 0
        assert stats["emails_sent"] == 0
        assert reminder_repo.count() == 0


def test_suspended_users_get_no_reminders(app, user, overdue_planting, monkeypatch):
    from app.models import UserStatus
    from app.repositories import user_repo
    from app.tasks import reminders

    monkeypatch.setattr(
        reminders.email_service, "send_care_digest",
        lambda *a, **k: pytest.fail("suspended users must not be emailed"),
    )

    with app.app_context():
        user_repo.set_status(user.id, UserStatus.SUSPENDED)
        stats = reminders.sweep_care_reminders(dry_run=False)

    assert stats["emails_sent"] == 0
