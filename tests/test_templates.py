"""Smoke tests: every page and every email template must actually render.

A Jinja typo (a renamed variable, a filter that doesn't exist, a missing macro
argument) only shows up when the template is rendered. These tests walk every
GET route and render every email, so a broken template fails here rather than
in front of a user.
"""

from __future__ import annotations

import pytest

# (url template, needs_admin)
# "/" is deliberately absent: it redirects signed-in visitors to the dashboard,
# and is covered by ANONYMOUS_PAGES below.
PAGES = [
    ("/dashboard/", False),
    ("/dashboard/plant/{planting_id}", False),
    ("/gardens/", False),
    ("/gardens/catalog", False),
    ("/gardens/{garden_id}/edit", False),
    ("/gardens/plants/{planting_id}/edit", False),
    ("/plants/request", False),
    ("/chat/", False),
    ("/profile/", False),
    ("/admin/", True),
    ("/admin/plants", True),
    ("/admin/plants/new", True),
    ("/admin/plants/{plant_id}/edit", True),
    ("/admin/requests", True),
    ("/admin/users", True),
]

ANONYMOUS_PAGES = [
    "/",
    "/auth/login",
    "/auth/signup",
    "/auth/forgot-password",
]


@pytest.fixture
def populated(app, admin, make_plant):
    """An admin account with a garden, a planting, and a pending request."""
    from app.services import catalog_service, garden_service

    species = make_plant(common_name="Smoke Test Fern")
    with app.app_context():
        garden = garden_service.ensure_default_garden(admin.id)
        planting, _ = garden_service.add_plant(
            user_id=admin.id, garden_id=garden.id, plant_id=species.id
        )
        catalog_service.submit_request(admin.id, "Wanted Plant", "Please add it")

    return {
        "user": admin,
        "garden_id": garden.id,
        "planting_id": planting.id,
        "plant_id": species.id,
    }


@pytest.mark.parametrize(("template", "needs_admin"), PAGES)
def test_every_page_renders(client, login, populated, template, needs_admin):
    login(populated["user"])
    url = template.format(**{k: v for k, v in populated.items() if k != "user"})
    response = client.get(url)
    assert response.status_code == 200, f"{url} returned {response.status_code}"
    assert b"</html>" in response.data, f"{url} produced a truncated page"


@pytest.mark.parametrize("url", ANONYMOUS_PAGES)
def test_public_pages_render(client, url):
    response = client.get(url)
    assert response.status_code == 200
    assert b"</html>" in response.data


def test_reset_password_page_renders(client, app, user):
    from app.services import auth_service

    with app.app_context():
        token = auth_service.generate_reset_token(user, app.config["SECRET_KEY"])
    response = client.get(f"/auth/reset-password/{token}")
    assert response.status_code == 200
    assert b"Choose a new password" in response.data


def test_dashboard_renders_every_urgency_bucket(client, login, app, admin, make_plant):
    """Overdue, due-today and upcoming cards all use the same macro."""
    from datetime import UTC, datetime, timedelta

    from app.repositories import garden_plant_repo
    from app.services import garden_service

    with app.app_context():
        garden = garden_service.ensure_default_garden(admin.id)
        for offset in (-3, 0, 5):
            species = make_plant(common_name=f"Bucket {offset}")
            planting, _ = garden_service.add_plant(
                user_id=admin.id, garden_id=garden.id, plant_id=species.id
            )
            due = datetime.now(UTC) + timedelta(days=offset)
            garden_plant_repo.update_fields(
                planting.id, admin.id, {"next_water_due": due, "next_fertilize_due": due}
            )

    login(admin)
    response = client.get("/dashboard/")
    assert response.status_code == 200
    body = response.data
    assert b"Overdue" in body
    assert b"Due today" in body
    assert b"Bucket -3" in body and b"Bucket 0" in body and b"Bucket 5" in body


def test_error_pages_render(client):
    response = client.get("/definitely-not-a-route")
    assert response.status_code == 404
    assert b"404" in response.data
    assert b"</html>" in response.data


# --- Emails ----------------------------------------------------------------
def test_emails_render_with_no_request_in_flight(app, user, overdue_planting):
    """Celery renders reminder emails from a bare app context.

    Regression test: an app-wide context processor read ``current_user``, which
    is ``None`` (not an anonymous user) outside a request, so every scheduled
    reminder failed with an AttributeError while the same template rendered
    fine inside ``test_request_context()``.
    """
    from app.extensions import mail
    from app.services import email_service
    from app.tasks.reminders import collect_due_tasks

    with app.app_context(), mail.record_messages() as outbox:
        tasks = collect_due_tasks()[user.id]
        assert email_service.send_care_digest(user, tasks) is True

    assert len(outbox) == 1
    message = outbox[0]
    assert "</html>" in message.html
    assert message.body  # the plain-text alternative rendered too
    assert "needs watered" not in message.subject  # past tense reads wrong here


def test_care_digest_email_renders_both_parts(app, user):
    from datetime import date

    from flask import render_template

    tasks = [
        {
            "plant_name": "Monstera",
            "action": "water",
            "action_label": "Water",
            "garden": "Balcony",
            "quantity": 1,
            "days_overdue": 2,
            "due_on": date(2026, 3, 10),
            "fertilizer_type": "Balanced 10-10-10",
        },
        {
            "plant_name": "Basil",
            "action": "fertilize",
            "action_label": "Fertilize",
            "garden": "Balcony",
            "quantity": 3,
            "days_overdue": 0,
            "due_on": date(2026, 3, 12),
            "fertilizer_type": "High-nitrogen 20-10-10",
        },
    ]
    context = {
        "user": user,
        "tasks": tasks,
        "base_url": "http://localhost:5000",
        "water": [tasks[0]],
        "fertilize": [tasks[1]],
        "overdue": [tasks[0]],
    }

    with app.test_request_context():
        html = render_template("email/care_digest.html", **context)
        text = render_template("email/care_digest.txt", **context)

    assert "Monstera" in html and "Basil" in html
    assert "2 day" in html  # the overdue count is surfaced
    assert "Monstera" in text and "Basil" in text


def test_password_reset_email_renders(app, user):
    from flask import render_template

    with app.test_request_context():
        html = render_template(
            "email/password_reset.html",
            user=user,
            reset_url="http://localhost:5000/auth/reset-password/abc",
            base_url="http://localhost:5000",
        )
        text = render_template(
            "email/password_reset.txt",
            user=user,
            reset_url="http://localhost:5000/auth/reset-password/abc",
            base_url="http://localhost:5000",
        )

    assert "reset-password/abc" in html
    assert "reset-password/abc" in text


def test_welcome_email_renders(app, user):
    from flask import render_template

    with app.test_request_context():
        html = render_template(
            "email/welcome.html", user=user, base_url="http://localhost:5000"
        )
    assert user.display_name.split()[0] in html


def test_digest_subject_summarises_without_listing_everything(app, user, monkeypatch):
    """One digest per user — the subject must scale past a couple of plants."""
    from app.services import email_service

    captured = {}

    def fake_send(**kwargs):
        captured.update(kwargs)
        return True

    monkeypatch.setattr(email_service, "send", fake_send)

    tasks = [
        {
            "plant_name": f"Plant {i}",
            "action": "water",
            "action_label": "Water",
            "garden": "G",
            "quantity": 1,
            "days_overdue": 1 if i < 3 else 0,
            "due_on": None,
            "fertilizer_type": "x",
        }
        for i in range(8)
    ]
    with app.app_context():
        email_service.send_care_digest(user, tasks)

    assert "8 plants" in captured["subject"]
    assert "3 overdue" in captured["subject"]
