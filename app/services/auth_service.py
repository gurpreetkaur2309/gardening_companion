"""Registration, login, password reset and Google account linking."""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import UTC, datetime
from typing import Any

from bson import ObjectId
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.security import generate_password_hash

from ..errors import ConflictError, NotFoundError, ValidationError
from ..models import User, UserRole, UserStatus
from ..repositories import user_repo

logger = logging.getLogger(__name__)

RESET_SALT = "password-reset"
RESET_MAX_AGE_SECONDS = 3600

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,29}$")
PHONE_RE = re.compile(r"^\+?[0-9]{10,15}$")
FULL_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z .'\-]{1,79}$")

# Deliberately modest: length carries far more entropy than a symbol quota, and
# hard symbol rules push people toward "Password1!".
MIN_PASSWORD_LENGTH = 10


def validate_password(password: str) -> None:
    if not password or len(password) < MIN_PASSWORD_LENGTH:
        raise ValidationError(
            f"Passwords must be at least {MIN_PASSWORD_LENGTH} characters."
        )
    if len(password) > 200:
        raise ValidationError("That password is too long.")
    if password.isdigit() or password.isalpha():
        raise ValidationError("Mix letters and numbers so the password isn't guessable.")
    if password.lower() in _COMMON_PASSWORDS:
        raise ValidationError("That password is too common. Pick something else.")


_COMMON_PASSWORDS = {
    "password12", "password123", "1234567890", "qwerty1234", "letmein123",
    "iloveyou12", "admin12345", "welcome123", "abc123abc1", "passw0rd12",
    "gardening1", "password1234", "qwertyuiop",
}


def _normalise_email(email: str) -> str:
    value = (email or "").strip().lower()
    if not EMAIL_RE.match(value):
        raise ValidationError("That doesn't look like a valid email address.")
    return value


def _validate_username(username: str) -> str:
    value = (username or "").strip()
    if not USERNAME_RE.match(value):
        raise ValidationError(
            "Usernames start with a letter, are 4-30 characters, and use only "
            "letters, numbers and underscores."
        )
    return value


def _validate_phone(phone: str | None) -> str | None:
    value = (phone or "").strip()
    if not value:
        return None
    if not PHONE_RE.match(value):
        raise ValidationError("Enter a phone number of 10-15 digits.")
    return value


def _validate_full_name(full_name: str) -> str:
    value = " ".join((full_name or "").split())
    if not FULL_NAME_RE.match(value):
        raise ValidationError("Enter your name using letters, spaces, hyphens or apostrophes.")
    return value


# ---------------------------------------------------------------------------
# Registration & login
# ---------------------------------------------------------------------------
def register(
    *,
    full_name: str,
    username: str,
    email: str,
    password: str,
    phone: str | None = None,
) -> User:
    """Create an account. Raises ``ValidationError`` / ``ConflictError``."""
    full_name = _validate_full_name(full_name)
    username = _validate_username(username)
    email = _normalise_email(email)
    phone = _validate_phone(phone)
    validate_password(password)

    # Pre-checks give a precise message; the unique indexes are what actually
    # guarantee correctness under concurrency.
    if user_repo.get_by_email(email):
        raise ConflictError("That email address is already registered.")
    if user_repo.get_by_username(username):
        raise ConflictError("That username is already taken.")
    if phone and user_repo.get_by_phone(phone):
        raise ConflictError("That phone number is already in use.")

    user = User(
        _id=ObjectId(),
        email=email,
        username=username,
        full_name=full_name,
        phone=phone,
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
    )
    user.set_password(password)
    user_repo.create(user)
    logger.info("Registered user %s", user.username)
    return user


def authenticate(identifier: str, password: str) -> User:
    """Verify credentials.

    The same message is returned whether the account is missing or the password
    is wrong, so the form can't be used to enumerate registered emails.
    """
    generic = "Those details don't match an account."

    user = user_repo.find_by_identifier(identifier or "")
    if user is None:
        raise ValidationError(generic)
    if not user.has_password:
        raise ValidationError(
            "This account signs in with Google. Use the 'Continue with Google' button."
        )
    if not user.check_password(password or ""):
        raise ValidationError(generic)
    if user.status != UserStatus.ACTIVE:
        raise ValidationError("This account has been suspended. Contact an administrator.")
    return user


def update_profile(user_id: ObjectId, fields: dict[str, Any]) -> User:
    updates: dict[str, Any] = {}

    if "full_name" in fields:
        updates["full_name"] = _validate_full_name(fields["full_name"])
    if "username" in fields:
        username = _validate_username(fields["username"])
        clash = user_repo.get_by_username(username)
        if clash and clash.id != user_id:
            raise ConflictError("That username is already taken.")
        updates["username"] = username
    if "phone" in fields:
        phone = _validate_phone(fields["phone"])
        if phone:
            clash = user_repo.get_by_phone(phone)
            if clash and clash.id != user_id:
                raise ConflictError("That phone number is already in use.")
        updates["phone"] = phone

    if updates:
        user_repo.update_fields(user_id, updates)

    user = user_repo.get(user_id)
    if user is None:
        raise NotFoundError("Account not found.")
    return user


def change_password(user_id: ObjectId, current_password: str, new_password: str) -> None:
    user = user_repo.get(user_id)
    if user is None:
        raise NotFoundError("Account not found.")
    if user.has_password and not user.check_password(current_password or ""):
        raise ValidationError("Your current password is incorrect.")
    validate_password(new_password)
    user_repo.set_password(user_id, generate_password_hash(new_password))
    logger.info("Password changed for user %s", user_id)


# ---------------------------------------------------------------------------
# Password reset
# ---------------------------------------------------------------------------
def _serializer(secret_key: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret_key)


def _password_stamp(user: User) -> str:
    """A fingerprint of the account's current password hash.

    Derived from the hash rather than from ``password_changed_at`` for two
    reasons: MongoDB stores datetimes only to millisecond resolution, so a
    timestamp read back never equals the in-memory one it was written from; and
    two password changes inside the same second would share a timestamp but
    never share a hash (Werkzeug salts every one). Only the digest travels in
    the token, so the token never carries any part of the hash itself.
    """
    if not user.password_hash:
        return ""
    return hashlib.sha256(user.password_hash.encode()).hexdigest()[:16]


def generate_reset_token(user: User, secret_key: str) -> str:
    """A single-use reset token.

    It carries a fingerprint of the account's ``password_changed_at``.  Once the
    password changes the fingerprint moves, so any token issued earlier —
    including one already used — stops verifying.  The old implementation signed
    only the email, so a token stayed replayable for its whole hour.
    """
    return _serializer(secret_key).dumps(
        {"uid": str(user.id), "pw": _password_stamp(user)}, salt=RESET_SALT
    )


def verify_reset_token(
    token: str, secret_key: str, *, max_age: int = RESET_MAX_AGE_SECONDS
) -> User:
    try:
        payload = _serializer(secret_key).loads(token, salt=RESET_SALT, max_age=max_age)
    except SignatureExpired as exc:
        raise ValidationError("That reset link has expired. Request a new one.") from exc
    except BadSignature as exc:
        raise ValidationError("That reset link is not valid. Request a new one.") from exc

    if not isinstance(payload, dict):
        raise ValidationError("That reset link is not valid. Request a new one.")

    try:
        user_id = ObjectId(payload.get("uid", ""))
    except Exception as exc:
        raise ValidationError("That reset link is not valid.") from exc

    user = user_repo.get(user_id)
    if user is None:
        raise ValidationError("That reset link is not valid.")

    if payload.get("pw") != _password_stamp(user):
        raise ValidationError("That reset link has already been used. Request a new one.")

    return user


def reset_password(token: str, new_password: str, secret_key: str) -> User:
    user = verify_reset_token(token, secret_key)
    validate_password(new_password)
    user_repo.set_password(user.id, generate_password_hash(new_password))
    logger.info("Password reset completed for user %s", user.id)
    refreshed = user_repo.get(user.id)
    return refreshed or user


# ---------------------------------------------------------------------------
# Google sign-in
# ---------------------------------------------------------------------------
def _unique_username_from(seed: str) -> str:
    """Derive a free username from a Google profile name or email local-part."""
    base = re.sub(r"[^A-Za-z0-9_]", "", (seed or "gardener").replace(" ", "_"))
    if not base or not base[0].isalpha():
        base = f"g{base}"
    base = base[:24] or "gardener"
    while len(base) < 4:
        base += "0"

    if not user_repo.get_by_username(base):
        return base
    for suffix in range(1, 1000):
        candidate = f"{base[:24]}{suffix}"
        if not user_repo.get_by_username(candidate):
            return candidate
    return f"{base[:16]}{ObjectId()!s:.8}"


def upsert_google_user(profile: dict[str, Any]) -> tuple[User, bool]:
    """Find or create the account behind a verified Google profile.

    Returns ``(user, created)``.  Matching is by the stable ``sub`` claim first,
    then by verified email so an existing password account links rather than
    being duplicated.
    """
    sub = profile.get("sub") or profile.get("id")
    email = (profile.get("email") or "").strip().lower()
    if not sub or not email:
        raise ValidationError("Google did not return enough profile information.")
    if profile.get("email_verified") is False:
        raise ValidationError("Your Google email address is not verified.")

    existing = user_repo.get_by_google_sub(str(sub))
    if existing:
        return existing, False

    by_email = user_repo.get_by_email(email)
    if by_email:
        user_repo.link_google(by_email.id, str(sub), profile.get("picture"))
        refreshed = user_repo.get(by_email.id)
        return (refreshed or by_email), False

    full_name = profile.get("name") or email.split("@")[0]
    user = User(
        _id=ObjectId(),
        email=email,
        username=_unique_username_from(profile.get("given_name") or email.split("@")[0]),
        full_name=full_name[:80],
        password_hash=None,  # Google-only account until they set a password
        google_sub=str(sub),
        avatar_url=profile.get("picture"),
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
        created_at=datetime.now(UTC),
    )
    user_repo.create(user)
    logger.info("Created account via Google sign-in: %s", user.username)
    return user, True
