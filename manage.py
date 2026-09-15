#!/usr/bin/env python3
"""Operational CLI.

    python manage.py ensure-indexes
    python manage.py seed-catalog [--file data/plants_seed.json]
    python manage.py create-admin [--email x --username y]
    python manage.py send-reminders [--dry-run]
    python manage.py demo-data --email you@example.com
    python manage.py routes
    python manage.py check
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from bson import ObjectId

from app import create_app
from app.db import ensure_indexes, get_db, ping

app = create_app()


def cmd_ensure_indexes(_args: argparse.Namespace) -> int:
    with app.app_context():
        created = ensure_indexes()
    print(f"Ensured {len(created)} indexes:")
    for name in created:
        print(f"  · {name}")
    return 0


def cmd_seed_catalog(args: argparse.Namespace) -> int:
    from app.models import Plant
    from app.models.enums import CareSource
    from app.models.plant import PlantCare
    from app.repositories import plant_repo

    path = Path(args.file)
    if not path.exists():
        print(f"No such file: {path}", file=sys.stderr)
        return 1

    records = json.loads(path.read_text())
    created = updated = 0

    with app.app_context():
        for record in records:
            plant = Plant(
                _id=ObjectId(),
                common_name=record["common_name"],
                scientific_name=record.get("scientific_name"),
                family=record.get("family"),
                genus=record.get("genus"),
                edible=bool(record.get("edible", False)),
                description=record.get("description"),
                sapling_description=record.get("sapling_description"),
                care=PlantCare.from_doc(record.get("care")),
                # Seed data is hand-checked, so it outranks anything the AI
                # would infer and is never overwritten by a lookup.
                care_source=CareSource.CURATED,
            )
            _, was_created = plant_repo.upsert_by_scientific_name(plant)
            if was_created:
                created += 1
            else:
                updated += 1

    print(f"Catalog seeded: {created} added, {updated} updated ({len(records)} in file).")
    return 0


def cmd_create_admin(args: argparse.Namespace) -> int:
    from app.models import UserRole
    from app.repositories import user_repo
    from app.services import auth_service

    email = args.email or input("Email: ").strip()
    username = args.username or input("Username: ").strip()
    full_name = args.name or input("Full name: ").strip()

    with app.app_context():
        existing = user_repo.get_by_email(email)
        if existing:
            if existing.is_admin:
                print(f"{email} is already an admin.")
                return 0
            user_repo.set_role(existing.id, UserRole.ADMIN)
            print(f"Promoted existing account {email} to admin.")
            return 0

        password = args.password or getpass.getpass("Password: ")
        if not args.password:
            confirm = getpass.getpass("Confirm password: ")
            if password != confirm:
                print("Passwords don't match.", file=sys.stderr)
                return 1

        try:
            user = auth_service.register(
                full_name=full_name, username=username, email=email, password=password
            )
            user_repo.set_role(user.id, UserRole.ADMIN)
        except Exception as exc:
            print(f"Could not create admin: {exc}", file=sys.stderr)
            return 1

    print(f"Admin created: {email}")
    return 0


def cmd_send_reminders(args: argparse.Namespace) -> int:
    from app.tasks.reminders import collect_due_tasks, sweep_care_reminders

    with app.app_context():
        if args.dry_run:
            by_user = collect_due_tasks()
            if not by_user:
                print("Nothing is due. No emails would be sent.")
                return 0
            from app.repositories import user_repo

            total = 0
            for user_id, tasks in by_user.items():
                user = user_repo.get(user_id)
                label = user.email if user else str(user_id)
                print(f"\n{label} — {len(tasks)} task(s):")
                for task in tasks:
                    overdue = (
                        f"{task['days_overdue']}d overdue"
                        if task["days_overdue"]
                        else "due today"
                    )
                    print(f"   · {task['action_label']:<12} {task['plant_name']:<28} {overdue}")
                total += len(tasks)
            print(f"\n[dry run] {len(by_user)} email(s), {total} task(s). Nothing sent.")
            return 0

        stats = sweep_care_reminders(dry_run=False)
        print(f"Sweep complete: {stats}")
    return 0


def cmd_demo_data(args: argparse.Namespace) -> int:
    """Populate one account with plants at varied due dates, for screenshots."""
    from app.models import AgeStage
    from app.repositories import garden_plant_repo, plant_repo, user_repo
    from app.services import garden_service

    with app.app_context():
        user = user_repo.get_by_email(args.email)
        if user is None:
            print(f"No account for {args.email}. Sign up first.", file=sys.stderr)
            return 1

        plants = plant_repo.list_all(limit=40)
        if len(plants) < 6:
            print("Seed the catalog first: python manage.py seed-catalog", file=sys.stderr)
            return 1

        balcony = garden_service.ensure_default_garden(user.id)
        try:
            indoor = garden_service.create_garden(user.id, "Indoor", location="Living room")
        except Exception:
            indoor = next(
                (g for g in garden_service.list_gardens(user.id) if g.name == "Indoor"),
                balcony,
            )

        # Spread the due dates so the dashboard shows all three urgency states.
        plan = [
            (plants[0], balcony, AgeStage.MATURE, 1, -4),
            (plants[1], balcony, AgeStage.YOUNG, 2, -1),
            (plants[2], balcony, AgeStage.MATURE, 1, 0),
            (plants[3], indoor, AgeStage.SEEDLING, 4, 0),
            (plants[4], indoor, AgeStage.MATURE, 1, 3),
            (plants[5], indoor, AgeStage.MATURE, 1, 6),
        ]

        created = 0
        for plant, garden, stage, quantity, offset_days in plan:
            planting, was_new = garden_service.add_plant(
                user_id=user.id,
                garden_id=garden.id,
                plant_id=plant.id,
                age_stage=str(stage),
                quantity=quantity,
            )
            if not was_new:
                continue
            due = datetime.now(UTC) + timedelta(days=offset_days)
            garden_plant_repo.update_fields(
                planting.id,
                user.id,
                {
                    "next_water_due": due,
                    "next_fertilize_due": due + timedelta(days=5),
                    "last_watered_at": due - timedelta(days=planting.schedule.watering_days),
                },
            )
            created += 1

        print(f"Demo data ready for {args.email}: {created} planting(s) across 2 gardens.")
    return 0


def cmd_routes(_args: argparse.Namespace) -> int:
    rules = sorted(app.url_map.iter_rules(), key=lambda r: str(r))
    for rule in rules:
        methods = ",".join(sorted(rule.methods - {"HEAD", "OPTIONS"}))
        print(f"{methods:<22} {rule!s:<46} {rule.endpoint}")
    print(f"\n{len(rules)} routes.")
    return 0


def cmd_check(_args: argparse.Namespace) -> int:
    """Report on every external dependency, without needing them all up."""
    from app.services.gemini_service import is_available
    from app.services.storage_service import get_storage

    ok = True
    with app.app_context():
        db_up = ping()
        host = app.config["MONGO_URI"].split("@")[-1]
        print(f"MongoDB      {'up' if db_up else 'DOWN'}  ({host})")
        ok = ok and db_up

        if db_up:
            db = get_db()
            for name in ("users", "plants", "gardens", "garden_plants"):
                print(f"  {name:<16} {db[name].count_documents({})} documents")

        print(f"Storage      {get_storage().name}")
        print(f"Gemini       {'enabled' if is_available() else 'disabled (no API key)'}")
        oauth = "configured" if app.config.get("GOOGLE_CLIENT_ID") else "not configured"
        print(f"Google OAuth {oauth}")
        print(f"SMTP         {app.config['MAIL_SERVER']}:{app.config['MAIL_PORT']}")

        try:
            import redis

            redis.Redis.from_url(app.config["CELERY_BROKER_URL"]).ping()
            print("Redis        up")
        except Exception as exc:
            print(f"Redis        DOWN ({type(exc).__name__}) — reminders won't run")

    return 0 if ok else 1


COMMANDS = {
    "ensure-indexes": (cmd_ensure_indexes, "Create every declared MongoDB index"),
    "seed-catalog": (cmd_seed_catalog, "Load the curated plant catalog"),
    "create-admin": (cmd_create_admin, "Create or promote an admin account"),
    "send-reminders": (cmd_send_reminders, "Run the care-reminder sweep now"),
    "demo-data": (cmd_demo_data, "Populate an account with sample plantings"),
    "routes": (cmd_routes, "List all registered routes"),
    "check": (cmd_check, "Report the status of every dependency"),
}


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="manage.py", description="Gardening Companion operations CLI"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    for name, (_, help_text) in COMMANDS.items():
        cmd = sub.add_parser(name, help=help_text)
        if name == "seed-catalog":
            cmd.add_argument("--file", default="data/plants_seed.json")
        elif name == "create-admin":
            cmd.add_argument("--email")
            cmd.add_argument("--username")
            cmd.add_argument("--name", default="Administrator")
            cmd.add_argument("--password", help="Prompted for if omitted")
        elif name == "send-reminders":
            cmd.add_argument(
                "--dry-run", action="store_true", help="Print what would be sent, send nothing"
            )
        elif name == "demo-data":
            cmd.add_argument("--email", required=True)

    args = parser.parse_args()
    handler, _ = COMMANDS[args.command]
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
