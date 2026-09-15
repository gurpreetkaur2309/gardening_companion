"""User persistence."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from bson import ObjectId
from pymongo import ASCENDING
from pymongo.errors import DuplicateKeyError

from ..errors import ConflictError
from ..models import User, UserRole, UserStatus
from .base import BaseRepository


def _escape(value: str) -> str:
    """Quote a value for use inside a regex — user input must not be a pattern."""
    return re.escape(value.strip())


class UserRepository(BaseRepository):
    collection_name = "users"

    # --- Lookups -------------------------------------------------------------
    def get(self, user_id: ObjectId) -> User | None:
        return User.from_doc(self.collection.find_one({"_id": user_id}))

    def get_by_email(self, email: str) -> User | None:
        return User.from_doc(self.collection.find_one({"email": email.strip().lower()}))

    def get_by_username(self, username: str) -> User | None:
        # Usernames are stored as typed but compared case-insensitively so
        # "Gurpreet" and "gurpreet" can't both be registered.
        pattern = f"^{_escape(username)}$"
        return User.from_doc(
            self.collection.find_one({"username": {"$regex": pattern, "$options": "i"}})
        )

    def get_by_phone(self, phone: str) -> User | None:
        return User.from_doc(self.collection.find_one({"phone": phone.strip()}))

    def get_by_google_sub(self, sub: str) -> User | None:
        return User.from_doc(self.collection.find_one({"google_sub": sub}))

    def find_by_identifier(self, identifier: str) -> User | None:
        """Log in with email, username or phone — whichever the user typed."""
        value = identifier.strip()
        pattern = f"^{_escape(value)}$"
        return User.from_doc(
            self.collection.find_one(
                {
                    "$or": [
                        {"email": value.lower()},
                        {"username": {"$regex": pattern, "$options": "i"}},
                        {"phone": value},
                    ]
                }
            )
        )

    # --- Writes --------------------------------------------------------------
    def create(self, user: User) -> User:
        doc = user.to_doc()
        doc["email"] = doc["email"].strip().lower()
        try:
            self.collection.insert_one(doc)
        except DuplicateKeyError as exc:
            raise ConflictError(self._describe_duplicate(exc)) from exc
        return user

    def update_fields(self, user_id: ObjectId, fields: dict[str, Any]) -> bool:
        fields = {**fields, "updated_at": datetime.now(UTC)}
        try:
            result = self.collection.update_one({"_id": user_id}, {"$set": fields})
        except DuplicateKeyError as exc:
            raise ConflictError(self._describe_duplicate(exc)) from exc
        return result.matched_count > 0

    def set_password(self, user_id: ObjectId, password_hash: str) -> bool:
        """Also stamps ``password_changed_at``, which invalidates reset links."""
        return self.update_fields(
            user_id,
            {"password_hash": password_hash, "password_changed_at": datetime.now(UTC)},
        )

    def set_role(self, user_id: ObjectId, role: str) -> bool:
        if role not in set(UserRole):
            raise ValueError(f"Unknown role: {role}")
        return self.update_fields(user_id, {"role": role})

    def set_status(self, user_id: ObjectId, status: str) -> bool:
        if status not in set(UserStatus):
            raise ValueError(f"Unknown status: {status}")
        return self.update_fields(user_id, {"status": status})

    def link_google(self, user_id: ObjectId, sub: str, avatar_url: str | None) -> bool:
        fields: dict[str, Any] = {"google_sub": sub}
        if avatar_url:
            fields["avatar_url"] = avatar_url
        return self.update_fields(user_id, fields)

    def delete(self, user_id: ObjectId) -> bool:
        return self.delete_by_id(user_id)

    # --- Listings ------------------------------------------------------------
    def list_all(
        self, *, search: str | None = None, limit: int = 100, skip: int = 0
    ) -> list[User]:
        query: dict[str, Any] = {}
        if search:
            pattern = _escape(search)
            query["$or"] = [
                {"full_name": {"$regex": pattern, "$options": "i"}},
                {"username": {"$regex": pattern, "$options": "i"}},
                {"email": {"$regex": pattern, "$options": "i"}},
            ]
        cursor = (
            self.collection.find(query).sort("created_at", ASCENDING).skip(skip).limit(limit)
        )
        return [u for u in (User.from_doc(d) for d in cursor) if u]

    def count_admins(self) -> int:
        return self.collection.count_documents({"role": UserRole.ADMIN})

    # --- Error shaping -------------------------------------------------------
    @staticmethod
    def _describe_duplicate(exc: DuplicateKeyError) -> str:
        detail = str(exc)
        if "email" in detail:
            return "That email address is already registered."
        if "username" in detail:
            return "That username is already taken."
        if "phone" in detail:
            return "That phone number is already in use."
        if "google_sub" in detail:
            return "That Google account is already linked to another user."
        return "Those details are already in use."
