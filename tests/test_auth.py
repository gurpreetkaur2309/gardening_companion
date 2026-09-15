"""Registration, login, and the password-reset token lifecycle."""

from __future__ import annotations

import pytest

from app.errors import ConflictError, ValidationError


# --- Registration ----------------------------------------------------------
def test_register_creates_a_usable_account(app):
    from app.services import auth_service

    with app.app_context():
        user = auth_service.register(
            full_name="Gurpreet Singh",
            username="gurpreet",
            email="  Gurpreet@Example.COM ",
            password="correct-horse-7",
        )
        assert user.email == "gurpreet@example.com"  # trimmed and lowercased
        assert user.check_password("correct-horse-7")
        assert user.password_hash and "correct-horse-7" not in user.password_hash


@pytest.mark.parametrize(
    "password",
    ["short1", "allletters", "1234567890", "password123"],
)
def test_weak_passwords_are_rejected(app, password):
    from app.services import auth_service

    with app.app_context(), pytest.raises(ValidationError):
        auth_service.register(
            full_name="Test User", username="tester", email="t@example.com", password=password
        )


def test_duplicate_email_is_rejected_case_insensitively(app, make_user):
    from app.services import auth_service

    make_user(email="taken@example.com")
    with app.app_context(), pytest.raises(ConflictError):
        auth_service.register(
            full_name="Someone Else",
            username="different",
            email="TAKEN@example.com",
            password="correct-horse-7",
        )


def test_duplicate_username_is_rejected_case_insensitively(app, make_user):
    from app.services import auth_service

    make_user(username="gurpreet")
    with app.app_context(), pytest.raises(ConflictError):
        auth_service.register(
            full_name="Someone Else",
            username="GURPREET",
            email="other@example.com",
            password="correct-horse-7",
        )


# --- Login -----------------------------------------------------------------
def test_login_accepts_email_username_or_phone(app, make_user):
    from app.services import auth_service

    make_user(email="multi@example.com", username="multiuser", phone="+911234567890")
    with app.app_context():
        for identifier in ("multi@example.com", "multiuser", "+911234567890"):
            assert auth_service.authenticate(identifier, "correct-horse-7")


def test_wrong_password_and_missing_account_give_the_same_message(app, make_user):
    """Otherwise the login form reveals which emails are registered."""
    from app.services import auth_service

    make_user(email="real@example.com")
    with app.app_context():
        with pytest.raises(ValidationError) as wrong_password:
            auth_service.authenticate("real@example.com", "not-the-password")
        with pytest.raises(ValidationError) as no_account:
            auth_service.authenticate("ghost@example.com", "not-the-password")

    assert str(wrong_password.value) == str(no_account.value)


def test_suspended_account_cannot_sign_in(app, make_user):
    from app.models import UserStatus
    from app.repositories import user_repo
    from app.services import auth_service

    target = make_user()
    with app.app_context():
        user_repo.set_status(target.id, UserStatus.SUSPENDED)
        with pytest.raises(ValidationError, match="suspended"):
            auth_service.authenticate(target.email, "correct-horse-7")


# --- Reset tokens ----------------------------------------------------------
def test_reset_token_round_trips(app, user):
    from app.services import auth_service

    with app.app_context():
        secret = app.config["SECRET_KEY"]
        token = auth_service.generate_reset_token(user, secret)
        assert auth_service.verify_reset_token(token, secret).id == user.id


def test_reset_token_is_single_use(app, user):
    """The token embeds password_changed_at, so using it invalidates it."""
    from app.services import auth_service

    with app.app_context():
        secret = app.config["SECRET_KEY"]
        token = auth_service.generate_reset_token(user, secret)

        auth_service.reset_password(token, "brand-new-pass-9", secret)

        with pytest.raises(ValidationError, match="already been used"):
            auth_service.verify_reset_token(token, secret)


def test_reset_token_from_another_secret_is_rejected(app, user):
    from app.services import auth_service

    with app.app_context():
        forged = auth_service.generate_reset_token(user, "an-attackers-key")
        with pytest.raises(ValidationError):
            auth_service.verify_reset_token(forged, app.config["SECRET_KEY"])


def test_expired_reset_token_is_rejected(app, user):
    from app.services import auth_service

    with app.app_context():
        secret = app.config["SECRET_KEY"]
        token = auth_service.generate_reset_token(user, secret)
        with pytest.raises(ValidationError, match="expired"):
            auth_service.verify_reset_token(token, secret, max_age=-1)


# --- Google sign-in --------------------------------------------------------
def test_google_links_to_an_existing_account_rather_than_duplicating(app, make_user):
    from app.services import auth_service

    existing = make_user(email="both@example.com")
    with app.app_context():
        user, created = auth_service.upsert_google_user(
            {"sub": "google-123", "email": "both@example.com", "name": "Both Ways",
             "email_verified": True}
        )
    assert created is False
    assert user.id == existing.id


def test_google_rejects_an_unverified_email(app):
    from app.services import auth_service

    with app.app_context(), pytest.raises(ValidationError):
        auth_service.upsert_google_user(
            {"sub": "x", "email": "spoof@example.com", "email_verified": False}
        )


def test_google_signup_generates_a_free_username(app, make_user):
    from app.services import auth_service

    make_user(username="gurpreet")
    with app.app_context():
        user, created = auth_service.upsert_google_user(
            {"sub": "google-456", "email": "new@example.com", "given_name": "gurpreet",
             "name": "Gurpreet S", "email_verified": True}
        )
    assert created is True
    assert user.username != "gurpreet"
