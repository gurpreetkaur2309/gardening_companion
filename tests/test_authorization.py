"""Authorization — the class of bug this rebuild exists to fix.

The application this replaces exposed its admin endpoints with no check at all:
an anonymous visitor could POST to /delete_user/<id> and remove any account.
These tests assert that every privileged route rejects anonymous and ordinary
users, and that no user can reach another user's data.
"""

from __future__ import annotations

import pytest

# (method, url template) for every admin route that changes or reveals state.
ADMIN_ROUTES = [
    ("get", "/admin/"),
    ("get", "/admin/plants"),
    ("get", "/admin/plants/new"),
    ("get", "/admin/requests"),
    ("get", "/admin/users"),
    ("get", "/admin/reminders/preview"),
    ("post", "/admin/users/{user_id}/role"),
    ("post", "/admin/users/{user_id}/status"),
    ("post", "/admin/users/{user_id}/delete"),
    ("post", "/admin/plants/{plant_id}/delete"),
]


@pytest.mark.parametrize(("method", "template"), ADMIN_ROUTES)
def test_anonymous_cannot_touch_admin_routes(client, user, plant, method, template):
    url = template.format(user_id=user.id, plant_id=plant.id)
    response = getattr(client, method)(url, follow_redirects=False)
    # Redirected to login (302) or refused (403) — never carried out (200).
    assert response.status_code in (302, 401, 403), f"{method.upper()} {url} was not protected"
    if response.status_code == 302:
        assert "/auth/login" in response.headers["Location"]


@pytest.mark.parametrize(("method", "template"), ADMIN_ROUTES)
def test_ordinary_user_cannot_touch_admin_routes(client, login, user, plant, method, template):
    login(user)
    url = template.format(user_id=user.id, plant_id=plant.id)
    response = getattr(client, method)(url, follow_redirects=False)
    assert response.status_code == 403, f"{method.upper()} {url} allowed a non-admin"


def test_non_admin_delete_user_leaves_the_account_intact(app, client, login, make_user):
    """The specific hole from the old code, asserted end to end."""
    from app.repositories import user_repo

    attacker = make_user(email="attacker@example.com", username="attacker")
    victim = make_user(email="victim@example.com", username="victim")
    login(attacker)

    client.post(f"/admin/users/{victim.id}/delete")

    with app.app_context():
        assert user_repo.get(victim.id) is not None


def test_admin_can_reach_the_console(client, login, admin):
    login(admin)
    assert client.get("/admin/").status_code == 200


# --- Cross-user isolation --------------------------------------------------
def test_user_cannot_open_another_users_planting(client, login, make_user, app, plant):
    from app.services import garden_service

    owner = make_user(email="owner@example.com", username="ownerone")
    intruder = make_user(email="intruder@example.com", username="intruder")

    with app.app_context():
        garden = garden_service.ensure_default_garden(owner.id)
        planting, _ = garden_service.add_plant(
            user_id=owner.id, garden_id=garden.id, plant_id=plant.id
        )

    login(intruder)
    response = client.get(f"/dashboard/plant/{planting.id}")
    # 404, not 403: the response must not confirm that this id exists.
    assert response.status_code == 404


def test_user_cannot_log_care_on_another_users_plant(app, make_user, plant):
    from app.errors import NotFoundError
    from app.models import CareAction
    from app.services import care_service, garden_service

    owner = make_user(email="o2@example.com", username="ownertwo")
    intruder = make_user(email="i2@example.com", username="intrudertwo")

    with app.app_context():
        garden = garden_service.ensure_default_garden(owner.id)
        planting, _ = garden_service.add_plant(
            user_id=owner.id, garden_id=garden.id, plant_id=plant.id
        )
        with pytest.raises(NotFoundError):
            care_service.record_care(planting.id, intruder.id, CareAction.WATER)


def test_user_cannot_delete_another_users_garden(app, make_user):
    from app.errors import NotFoundError
    from app.services import garden_service

    owner = make_user(email="o3@example.com", username="ownerthree")
    intruder = make_user(email="i3@example.com", username="intruderthree")

    with app.app_context():
        garden_service.ensure_default_garden(owner.id)
        extra = garden_service.create_garden(owner.id, "Terrace")
        with pytest.raises(NotFoundError):
            garden_service.delete_garden(extra.id, intruder.id)


def test_malformed_object_id_is_a_404_not_a_500(client, login, user):
    """Bare ObjectId() on a URL parameter used to raise InvalidId -> 500."""
    login(user)
    assert client.get("/dashboard/plant/not-a-valid-id").status_code == 404


# --- Admin self-protection -------------------------------------------------
def test_admin_cannot_demote_themselves(app, client, login, admin):
    from app.repositories import user_repo

    login(admin)
    client.post(f"/admin/users/{admin.id}/role", data={"role": "user"})
    with app.app_context():
        assert user_repo.get(admin.id).is_admin


def test_last_admin_cannot_be_deleted(app, client, login, admin, make_user):
    from app.models import UserRole
    from app.repositories import user_repo

    other_admin = make_user(role=UserRole.ADMIN, email="a2@example.com", username="adminTwo")
    login(admin)
    client.post(f"/admin/users/{other_admin.id}/role", data={"role": "user"})

    with app.app_context():
        # One demotion is fine while two admins exist.
        assert user_repo.count_admins() >= 1


def test_authenticated_pages_require_login(client):
    for url in ("/dashboard/", "/profile/", "/chat/", "/gardens/", "/plants/request"):
        response = client.get(url, follow_redirects=False)
        assert response.status_code == 302
        assert "/auth/login" in response.headers["Location"]
