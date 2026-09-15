"""End-to-end checks through the HTTP layer."""

from __future__ import annotations


def test_landing_page_renders_for_anonymous_visitors(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"Gardening Companion" in response.data


def test_landing_page_survives_a_database_outage(client, monkeypatch):
    """The public page must still render when MongoDB is unreachable — the
    species count on it is decorative, not load-bearing."""
    from app.repositories import plant_repo

    def boom():
        raise RuntimeError("no database")

    monkeypatch.setattr(plant_repo, "count", boom)

    response = client.get("/")
    assert response.status_code == 200
    assert b"Gardening Companion" in response.data
    assert b"species in the catalog" not in response.data


def test_healthz_reports_dependencies(client):
    response = client.get("/healthz")
    assert response.status_code in (200, 503)
    payload = response.get_json()
    assert set(payload) >= {"status", "database", "ai", "storage"}


def test_signup_login_logout_round_trip(client, app):
    signup = client.post(
        "/auth/signup",
        data={
            "full_name": "Gurpreet Singh",
            "username": "gurpreet",
            "email": "gurpreet@example.com",
            "phone": "",
            "password": "correct-horse-7",
            "confirm": "correct-horse-7",
        },
        follow_redirects=True,
    )
    assert signup.status_code == 200
    assert b"Hello, Gurpreet" in signup.data

    client.get("/auth/logout", follow_redirects=True)
    assert client.get("/dashboard/", follow_redirects=False).status_code == 302

    login = client.post(
        "/auth/login",
        data={"identifier": "gurpreet@example.com", "password": "correct-horse-7"},
        follow_redirects=True,
    )
    assert b"Hello, Gurpreet" in login.data


def test_signup_rejects_a_mismatched_confirmation(client):
    response = client.post(
        "/auth/signup",
        data={
            "full_name": "Test User",
            "username": "tester2",
            "email": "t2@example.com",
            "password": "correct-horse-7",
            "confirm": "different-pass-8",
        },
    )
    assert b"Passwords don" in response.data


def test_dashboard_shows_an_empty_state_for_a_new_account(client, login, user):
    login(user)
    response = client.get("/dashboard/")
    assert response.status_code == 200
    assert b"No plants in" in response.data


def test_dashboard_groups_an_overdue_plant(client, login, user, overdue_planting):
    login(user)
    response = client.get("/dashboard/")
    assert response.status_code == 200
    assert b"Overdue" in response.data
    assert b"overdue" in response.data.lower()


def test_watering_from_the_dashboard_updates_the_card(client, login, app, user, overdue_planting):
    from app.repositories import garden_plant_repo

    login(user)
    response = client.post(
        f"/dashboard/plant/{overdue_planting.id}/care",
        data={"action": "water"},
        follow_redirects=True,
    )
    assert response.status_code == 200

    with app.app_context():
        refreshed = garden_plant_repo.get(overdue_planting.id, user.id)
        assert refreshed.days_until_water > 0


def test_forgot_password_never_reveals_whether_an_account_exists(client, user):
    known = client.post(
        "/auth/forgot-password", data={"email": user.email}, follow_redirects=True
    )
    unknown = client.post(
        "/auth/forgot-password", data={"email": "ghost@example.com"}, follow_redirects=True
    )
    assert b"on its way" in known.data
    assert b"on its way" in unknown.data


def test_security_headers_are_present(client):
    headers = client.get("/").headers
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"
    assert "Content-Security-Policy" in headers
    assert "X-Request-ID" in headers


def test_csrf_is_enforced_when_enabled(app, user, login):
    """The testing config disables CSRF; turn it on and confirm it bites."""
    app.config["WTF_CSRF_ENABLED"] = True
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = str(user.id)
        session["_fresh"] = True

    response = client.post("/gardens/create", data={"name": "Sneaky"})
    assert response.status_code == 400


# --- JSON API --------------------------------------------------------------
def test_api_requires_authentication(client):
    response = client.get("/api/v1/me")
    assert response.status_code in (302, 401)


def test_api_me_returns_a_summary(client, login, user):
    login(user)
    payload = client.get("/api/v1/me").get_json()
    assert payload["user"]["email"] == user.email
    assert "summary" in payload
    assert "password_hash" not in payload["user"]  # never serialised


def test_api_due_groups_by_urgency(client, login, user, overdue_planting):
    login(user)
    payload = client.get("/api/v1/plants/due").get_json()
    assert payload["counts"]["overdue"] == 1
    assert payload["overdue"][0]["days_until_water"] < 0


def test_api_catalog_search(client, login, user, make_plant):
    make_plant(common_name="Findable Fern")
    make_plant(common_name="Hidden Hosta")
    login(user)

    payload = client.get("/api/v1/catalog?q=findable").get_json()
    names = [p["common_name"] for p in payload["plants"]]
    assert "Findable Fern" in names
    assert "Hidden Hosta" not in names


def test_404_returns_json_for_api_clients(client, login, user):
    login(user)
    response = client.get("/api/v1/plants/000000000000000000000000")
    assert response.status_code == 404
    assert response.get_json()["error"]


# --- Chat ------------------------------------------------------------------
def test_chat_send_returns_a_reply(client, login, user, fake_gemini):
    login(user)
    response = client.post("/chat/send", json={"message": "How often do I water basil?"})
    assert response.status_code == 200
    assert response.get_json()["reply"]


def test_chat_rejects_an_empty_message(client, login, user, fake_gemini):
    login(user)
    assert client.post("/chat/send", json={"message": "   "}).status_code == 400


def test_chat_rejects_an_overlong_message(client, login, user, fake_gemini):
    login(user)
    response = client.post("/chat/send", json={"message": "x" * 2000})
    assert response.status_code == 400


def test_chat_persists_both_sides_of_the_conversation(client, login, app, user, fake_gemini):
    from app.repositories import chat_repo

    login(user)
    client.post("/chat/send", json={"message": "Hello"})

    with app.app_context():
        history = chat_repo.history(user.id)
    assert [m["role"] for m in history] == ["user", "model"]


def test_chat_passes_the_users_garden_as_context(client, login, user, overdue_planting, fake_gemini):
    login(user)
    client.post("/chat/send", json={"message": "What needs doing?"})

    system = fake_gemini.calls[-1].get("system_instruction", "")
    assert "THE USER'S GARDEN" in system
    assert "My Garden" in system
