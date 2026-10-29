"""MongoDB connection handling and index management.

One :class:`~pymongo.MongoClient` per process (it is thread-safe and pools
internally).  Indexes are declared here in one place and applied by
``python manage.py ensure-indexes`` — never implicitly on request, which would
add latency to every boot and race between workers.
"""





from __future__ import annotations

import logging
from typing import Any

from flask import Flask, current_app, g
from pymongo import ASCENDING, DESCENDING, TEXT, MongoClient
from pymongo.database import Database
from pymongo.errors import OperationFailure

logger = logging.getLogger(__name__)

_client: MongoClient | None = None


def init_app(app: Flask) -> None:
    """Attach a Mongo client to the app and close it on teardown."""
    global _client
    if _client is None:
        _client = MongoClient(
            app.config["MONGO_URI"],
            serverSelectionTimeoutMS=app.config["MONGO_SERVER_SELECTION_TIMEOUT_MS"],
            tz_aware=True,
            appname="gardening-companion",
        )
    app.extensions["mongo_client"] = _client

    @app.teardown_appcontext
    def _clear_db(_exc: BaseException | None) -> None:
        g.pop("_mongo_db", None)


def get_client() -> MongoClient:
    client = current_app.extensions.get("mongo_client")
    if client is None:  # pragma: no cover - misconfiguration guard
        raise RuntimeError("Mongo client not initialised; call db.init_app(app) first.")
    return client


def get_db() -> Database:
    """The application database for the current app context."""
    if "_mongo_db" not in g:
        g._mongo_db = get_client()[current_app.config["MONGO_DB_NAME"]]
    return g._mongo_db


def ping() -> bool:
    """Cheap liveness check used by ``/healthz``.

    Reporting "down" is this function's job, not an exceptional event, so the
    failure is logged as one line. The traceback goes to DEBUG for whoever is
    actually diagnosing it.
    """
    try:
        get_client().admin.command("ping")
        return True
    except Exception as exc:
        logger.warning("MongoDB unreachable: %s", type(exc).__name__)
        logger.debug("MongoDB ping traceback", exc_info=True)
        return False


# --------------------------------------------------------------------------
# Index definitions
# --------------------------------------------------------------------------
# (collection, keys, options).  Partial filters keep uniqueness from tripping on
# the many documents that legitimately have no phone / no google_sub.
INDEXES: list[tuple[str, list[tuple[str, Any]], dict[str, Any]]] = [
    # --- users ---
    ("users", [("email", ASCENDING)], {"unique": True, "name": "uniq_email"}),
    ("users", [("username", ASCENDING)], {"unique": True, "name": "uniq_username"}),
    (
        "users",
        [("phone", ASCENDING)],
        {
            "unique": True,
            "name": "uniq_phone",
            "partialFilterExpression": {"phone": {"$type": "string"}},
        },
    ),
    (
        "users",
        [("google_sub", ASCENDING)],
        {
            "unique": True,
            "name": "uniq_google_sub",
            "partialFilterExpression": {"google_sub": {"$type": "string"}},
        },
    ),
    # --- plants (shared catalog) ---
    (
        "plants",
        [("scientific_name", ASCENDING)],
        {
            "unique": True,
            "name": "uniq_scientific_name",
            "partialFilterExpression": {"scientific_name": {"$type": "string"}},
        },
    ),
    (
        "plants",
        [("common_name", TEXT), ("scientific_name", TEXT), ("genus", TEXT)],
        {"name": "text_plant_search", "default_language": "english"},
    ),
    ("plants", [("common_name", ASCENDING)], {"name": "idx_common_name"}),
    # --- gardens ---
    (
        "gardens",
        [("user_id", ASCENDING), ("name", ASCENDING)],
        {"unique": True, "name": "uniq_user_garden_name"},
    ),
    # --- garden_plants (a user's individual plantings) ---
    (
        "garden_plants",
        [("user_id", ASCENDING), ("garden_id", ASCENDING)],
        {"name": "idx_user_garden"},
    ),
    ("garden_plants", [("garden_id", ASCENDING)], {"name": "idx_garden"}),
    (
        "garden_plants",
        [("reminders_enabled", ASCENDING), ("next_water_due", ASCENDING)],
        {"name": "idx_water_due"},
    ),
    (
        "garden_plants",
        [("reminders_enabled", ASCENDING), ("next_fertilize_due", ASCENDING)],
        {"name": "idx_fertilize_due"},
    ),
    # --- care_logs ---
    (
        "care_logs",
        [("garden_plant_id", ASCENDING), ("created_at", DESCENDING)],
        {"name": "idx_plant_history"},
    ),
    (
        "care_logs",
        [("user_id", ASCENDING), ("created_at", DESCENDING)],
        {"name": "idx_user_history"},
    ),
    # --- plant_requests ---
    (
        "plant_requests",
        [("status", ASCENDING), ("created_at", DESCENDING)],
        {"name": "idx_status_created"},
    ),
    # --- chat_messages: expire after 90 days ---
    (
        "chat_messages",
        [("user_id", ASCENDING), ("created_at", ASCENDING)],
        {"name": "idx_user_chat"},
    ),
    (
        "chat_messages",
        [("created_at", ASCENDING)],
        {"name": "ttl_chat", "expireAfterSeconds": 90 * 24 * 3600},
    ),
    # --- reminder_log: the idempotency guard that stops duplicate emails ---
    (
        "reminder_log",
        [("garden_plant_id", ASCENDING), ("action", ASCENDING), ("due_on", ASCENDING)],
        {"unique": True, "name": "uniq_reminder"},
    ),
    (
        "reminder_log",
        [("sent_at", ASCENDING)],
        {"name": "ttl_reminder_log", "expireAfterSeconds": 180 * 24 * 3600},
    ),
]


def ensure_indexes(db: Database | None = None) -> list[str]:
    """Create every declared index. Idempotent; safe to run on each deploy."""
    database = db if db is not None else get_db()
    created: list[str] = []
    for collection, keys, options in INDEXES:
        try:
            name = database[collection].create_index(keys, **options)
            created.append(f"{collection}.{name}")
        except OperationFailure as exc:
            # Most often: an index with the same name but different options
            # already exists.  Report it rather than dying mid-deploy.
            logger.error(
                "Could not create index %s on %s: %s", options.get("name"), collection, exc
            )
    return created
