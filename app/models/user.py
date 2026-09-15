"""The user account model."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from bson import ObjectId
from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from .enums import UserRole, UserStatus


@dataclass
class User(UserMixin):
    """An account.

    ``UserMixin`` supplies ``is_authenticated`` / ``is_anonymous``; ``is_active``
    is overridden below because a suspended account must not hold a session.
    """

    _id: ObjectId
    email: str
    username: str
    full_name: str
    password_hash: str | None = None
    phone: str | None = None
    role: str = UserRole.USER
    status: str = UserStatus.ACTIVE
    google_sub: str | None = None
    avatar_url: str | None = None
    # Bumped on every password change so outstanding reset links stop working.
    password_changed_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime | None = None

    # --- Flask-Login contract ------------------------------------------------
    def get_id(self) -> str:
        return str(self._id)

    @property
    def is_active(self) -> bool:  # type: ignore[override]
        return self.status == UserStatus.ACTIVE

    # --- Convenience ---------------------------------------------------------
    @property
    def id(self) -> ObjectId:
        return self._id

    @property
    def is_admin(self) -> bool:
        return self.role == UserRole.ADMIN

    @property
    def display_name(self) -> str:
        return self.full_name or self.username or self.email.split("@")[0]

    @property
    def initials(self) -> str:
        parts = [p for p in (self.full_name or self.username or "?").split() if p]
        if not parts:
            return "?"
        if len(parts) == 1:
            return parts[0][:2].upper()
        return (parts[0][0] + parts[-1][0]).upper()

    @property
    def has_password(self) -> bool:
        """False for accounts created purely through Google sign-in."""
        return bool(self.password_hash)

    # --- Passwords -----------------------------------------------------------
    def set_password(self, raw: str) -> None:
        self.password_hash = generate_password_hash(raw)
        self.password_changed_at = datetime.now(UTC)

    def check_password(self, raw: str) -> bool:
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, raw)

    # --- Serialisation -------------------------------------------------------
    @classmethod
    def from_doc(cls, doc: dict[str, Any] | None) -> User | None:
        if not doc:
            return None
        return cls(
            _id=doc["_id"],
            email=doc["email"],
            username=doc["username"],
            full_name=doc.get("full_name", ""),
            password_hash=doc.get("password_hash"),
            phone=doc.get("phone"),
            role=doc.get("role", UserRole.USER),
            status=doc.get("status", UserStatus.ACTIVE),
            google_sub=doc.get("google_sub"),
            avatar_url=doc.get("avatar_url"),
            password_changed_at=doc.get("password_changed_at"),
            created_at=doc.get("created_at") or datetime.now(UTC),
            updated_at=doc.get("updated_at"),
        )

    def to_doc(self) -> dict[str, Any]:
        return {
            "_id": self._id,
            "email": self.email,
            "username": self.username,
            "full_name": self.full_name,
            "password_hash": self.password_hash,
            "phone": self.phone,
            "role": str(self.role),
            "status": str(self.status),
            "google_sub": self.google_sub,
            "avatar_url": self.avatar_url,
            "password_changed_at": self.password_changed_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def to_public_dict(self) -> dict[str, Any]:
        """Safe to serialise into an API response — no hash, no reset state."""
        return {
            "id": str(self._id),
            "email": self.email,
            "username": self.username,
            "full_name": self.full_name,
            "phone": self.phone,
            "role": str(self.role),
            "status": str(self.status),
            "avatar_url": self.avatar_url,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
