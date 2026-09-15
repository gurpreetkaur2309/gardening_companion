"""The scheduled care-reminder sweep.

Runs every ``REMINDER_SWEEP_MINUTES``.  It finds every planting whose watering
or fertilizing is due, groups the tasks by user, and sends each user a single
digest email.

Duplicate suppression is the interesting part.  Before a task is included in an
email it is *claimed* in the ``reminder_log`` collection, whose unique index on
``(garden_plant_id, action, due_on)`` means only one caller can ever win.  A
retried task, an overlapping sweep or a second beat process therefore cannot
email anyone twice about the same due date.  If the send then fails, the claims
are released so the next sweep retries.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from celery import shared_task

from ..models import CareAction
from ..repositories import (
    garden_plant_repo,
    garden_repo,
    plant_repo,
    reminder_repo,
    user_repo,
)
from ..services import email_service

logger = logging.getLogger(__name__)

ACTION_DUE_FIELD = {
    CareAction.WATER: "next_water_due",
    CareAction.FERTILIZE: "next_fertilize_due",
}


def collect_due_tasks(now: datetime | None = None) -> dict[Any, list[dict[str, Any]]]:
    """Group every currently-due care task by user id.

    Pure read — claims nothing and sends nothing, so ``--dry-run`` and the real
    sweep see exactly the same set.
    """
    moment = now or datetime.now(UTC)
    plantings = garden_plant_repo.list_due(now=moment)
    if not plantings:
        return {}

    species = plant_repo.get_many([p.plant_id for p in plantings])
    garden_names: dict[Any, str] = {}

    by_user: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for planting in plantings:
        planting.plant = species.get(planting.plant_id)

        if planting.garden_id not in garden_names:
            garden = garden_repo.get(planting.garden_id, planting.user_id)
            garden_names[planting.garden_id] = garden.name if garden else "Your garden"

        for action, field in ACTION_DUE_FIELD.items():
            due = getattr(planting, field)
            if due is None:
                continue
            if due.tzinfo is None:
                due = due.replace(tzinfo=UTC)
            if due > moment:
                continue

            by_user[planting.user_id].append(
                {
                    "planting_id": planting.id,
                    "plant_name": planting.display_name,
                    "species": planting.plant.common_name if planting.plant else None,
                    "garden": garden_names[planting.garden_id],
                    "quantity": planting.quantity,
                    "action": str(action),
                    "action_label": action.label,
                    "due_on": due.date(),
                    "days_overdue": max(0, (moment.date() - due.date()).days),
                    "fertilizer_type": planting.schedule.fertilizer_type,
                }
            )

    return dict(by_user)


@shared_task(name="tasks.sweep_care_reminders", bind=True, max_retries=2)
def sweep_care_reminders(self, dry_run: bool = False) -> dict[str, int]:
    """Find due plants, claim them, and email one digest per user."""
    started = datetime.now(UTC)
    by_user = collect_due_tasks(started)

    stats = {"users": 0, "tasks": 0, "emails_sent": 0, "skipped_duplicates": 0, "failed": 0}
    if not by_user:
        logger.info("Reminder sweep: nothing due")
        return stats

    for user_id, tasks in by_user.items():
        user = user_repo.get(user_id)
        if user is None or not user.is_active:
            continue

        if dry_run:
            stats["users"] += 1
            stats["tasks"] += len(tasks)
            logger.info(
                "[dry-run] %s would receive %d task(s): %s",
                user.email,
                len(tasks),
                ", ".join(f"{t['plant_name']}:{t['action']}" for t in tasks),
            )
            continue

        # Claim first. Anything already claimed was covered by an earlier run.
        claimed: list[dict[str, Any]] = []
        for task in tasks:
            won = reminder_repo.claim(
                garden_plant_id=task["planting_id"],
                action=task["action"],
                due_on=task["due_on"],
                user_id=user_id,
            )
            if won:
                claimed.append(task)
            else:
                stats["skipped_duplicates"] += 1

        if not claimed:
            continue

        claimed.sort(key=lambda t: (-t["days_overdue"], t["plant_name"]))

        if email_service.send_care_digest(user, claimed):
            stats["users"] += 1
            stats["tasks"] += len(claimed)
            stats["emails_sent"] += 1
        else:
            # Release so the next sweep tries again rather than silently
            # swallowing the reminder.
            for task in claimed:
                reminder_repo.release(
                    garden_plant_id=task["planting_id"],
                    action=task["action"],
                    due_on=task["due_on"],
                )
            stats["failed"] += 1

    logger.info(
        "Reminder sweep finished in %.2fs: %s",
        (datetime.now(UTC) - started).total_seconds(),
        stats,
    )
    return stats


@shared_task(name="tasks.send_welcome_email")
def send_welcome_email(user_id: str) -> bool:
    """Queued after signup so registration doesn't block on SMTP."""
    from bson import ObjectId

    user = user_repo.get(ObjectId(user_id))
    if user is None:
        return False
    return email_service.send_welcome(user)


@shared_task(name="tasks.send_password_reset_email")
def send_password_reset_email(user_id: str, reset_url: str) -> bool:
    from bson import ObjectId

    user = user_repo.get(ObjectId(user_id))
    if user is None:
        return False
    return email_service.send_password_reset(user, reset_url)
