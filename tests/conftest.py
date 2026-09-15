"""Test fixtures.

Runs against mongomock by default so ``pytest`` works with no services running.
Set ``TEST_MONGO_URI`` to point at a real MongoDB (CI does) and the same suite
exercises the real driver, including the unique indexes that several tests
depend on.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest
from bson import ObjectId

os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")

REAL_MONGO_URI = os.getenv("TEST_MONGO_URI")
USING_REAL_MONGO = bool(REAL_MONGO_URI)


@pytest.fixture(scope="session")
def mongo_client():
    if REAL_MONGO_URI:
        from pymongo import MongoClient

        client = MongoClient(REAL_MONGO_URI, tz_aware=True, serverSelectionTimeoutMS=5000)
        client.admin.command("ping")
        yield client
        client.close()
    else:
        import mongomock

        yield mongomock.MongoClient(tz_aware=True)


@pytest.fixture
def app(mongo_client, monkeypatch, tmp_path):
    """A fresh application with an empty database per test."""
    from app import create_app
    from app.db import ensure_indexes

    db_name = f"gc_test_{ObjectId()}"
    monkeypatch.setenv("MONGO_DB_NAME", db_name)
    monkeypatch.setenv("LOCAL_UPLOAD_DIR_TEST", str(tmp_path / "uploads"))

    flask_app = create_app("testing")
    flask_app.config.update(
        MONGO_DB_NAME=db_name,
        LOCAL_UPLOAD_DIR=str(tmp_path / "uploads"),
        SERVER_NAME="localhost",
    )
    # Swap in the shared test client rather than the one the factory built.
    flask_app.extensions["mongo_client"] = mongo_client

    with flask_app.app_context():
        # Built for mongomock too: several tests depend on the unique indexes
        # (garden names, reminder claims) actually being enforced.
        ensure_indexes()

    yield flask_app

    mongo_client.drop_database(db_name)


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def db(app):
    from app.db import get_db

    with app.app_context():
        yield get_db()


# ---------------------------------------------------------------------------
# Domain fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def make_user(app):
    """Factory creating users directly, bypassing the signup form."""
    from app.models import User, UserRole
    from app.repositories import user_repo

    counter = {"n": 0}

    def _make(
        *, email=None, username=None, role=UserRole.USER, password="correct-horse-7", **kwargs
    ):
        counter["n"] += 1
        n = counter["n"]
        user = User(
            _id=ObjectId(),
            email=email or f"user{n}@example.com",
            username=username or f"user{n}name",
            full_name=kwargs.pop("full_name", f"Test User {n}"),
            role=role,
            **kwargs,
        )
        user.set_password(password)
        with app.app_context():
            user_repo.create(user)
        return user

    return _make


@pytest.fixture
def user(make_user):
    return make_user()


@pytest.fixture
def admin(make_user):
    from app.models import UserRole

    return make_user(role=UserRole.ADMIN, email="admin@example.com", username="adminuser")


@pytest.fixture
def login(client):
    """Log a user in by writing the Flask-Login session key directly.

    Avoids depending on the login form in tests that are about something else.
    """

    def _login(target):
        with client.session_transaction() as session:
            session["_user_id"] = str(target.id)
            session["_fresh"] = True
        return target

    return _login


@pytest.fixture
def make_plant(app):
    from app.models import Plant
    from app.models.enums import CareSource
    from app.models.plant import PlantCare
    from app.repositories import plant_repo

    counter = {"n": 0}

    def _make(*, common_name=None, watering_days=7, fertilizing_days=30, **kwargs):
        counter["n"] += 1
        n = counter["n"]
        plant = Plant(
            _id=ObjectId(),
            common_name=common_name or f"Test Plant {n}",
            scientific_name=kwargs.pop("scientific_name", f"Testus plantus{n}"),
            care=PlantCare(
                watering_days=watering_days, fertilizing_days=fertilizing_days
            ),
            care_source=kwargs.pop("care_source", CareSource.CURATED),
            **kwargs,
        )
        with app.app_context():
            plant_repo.create(plant)
        return plant

    return _make


@pytest.fixture
def plant(make_plant):
    return make_plant()


@pytest.fixture
def garden(app, user):
    from app.services import garden_service

    with app.app_context():
        return garden_service.ensure_default_garden(user.id)


@pytest.fixture
def planting(app, user, garden, plant):
    """One mature plant, watered today, so nothing is due."""
    from app.services import garden_service

    with app.app_context():
        created, _ = garden_service.add_plant(
            user_id=user.id, garden_id=garden.id, plant_id=plant.id
        )
        return created


@pytest.fixture
def overdue_planting(app, user, garden, plant):
    """A plant whose watering went overdue three days ago."""
    from app.repositories import garden_plant_repo
    from app.services import garden_service

    with app.app_context():
        created, _ = garden_service.add_plant(
            user_id=user.id, garden_id=garden.id, plant_id=plant.id
        )
        past = datetime.now(UTC) - timedelta(days=3)
        garden_plant_repo.update_fields(
            created.id,
            user.id,
            {"next_water_due": past, "next_fertilize_due": past + timedelta(days=10)},
        )
        return garden_plant_repo.get(created.id, user.id)


@pytest.fixture
def fake_gemini(app):
    """Replace the Gemini client with one returning canned answers."""

    class FakeClient:
        def __init__(self):
            self.calls = []
            self.next_response: str | None = None

        def generate(self, contents, **kwargs):
            self.calls.append({"contents": contents, **kwargs})
            if self.next_response is not None:
                return self.next_response
            if kwargs.get("response_schema") is not None:
                return (
                    '{"watering_days": 5, "fertilizing_days": 20, "sunlight": "full", '
                    '"fertilizer_type": "Test feed", "notes": "A test note."}'
                )
            return "A helpful test answer about plants."

    fake = FakeClient()
    app.extensions["gemini"] = fake
    return fake
