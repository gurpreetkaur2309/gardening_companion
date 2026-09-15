"""Authorization decorators and response hardening.

The app this replaces exposed ``/delete_user/<id>`` and ``/update_user_role/<id>``
with no check at all — any anonymous visitor could delete any account.  Every
privileged view here carries an explicit role decorator, and every object-scoped
operation re-checks ownership against the database rather than trusting an id
that arrived in the URL.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any, TypeVar

from bson import ObjectId
from bson.errors import InvalidId
from flask import Flask, abort, flash, redirect, request, url_for
from flask_login import current_user

from .errors import NotFoundError, PermissionDeniedError

F = TypeVar("F", bound=Callable[..., Any])


def roles_required(*roles: str) -> Callable[[F], F]:
    """Allow the view only for authenticated users holding one of ``roles``.

    Anonymous users are sent to the login page (with ``next``); authenticated
    users lacking the role get a 403 — not a redirect, which would hide the
    fact that the resource exists behind a confusing bounce.
    """

    def decorator(view: F) -> F:
        @functools.wraps(view)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if not current_user.is_authenticated:
                flash("Please log in to continue.", "info")
                return redirect(url_for("auth.login", next=request.full_path))
            if roles and getattr(current_user, "role", None) not in roles:
                abort(403)
            if getattr(current_user, "status", "active") != "active":
                flash("Your account is not active. Contact an administrator.", "danger")
                abort(403)
            return view(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator


admin_required = roles_required("admin")


def parse_object_id(value: str | ObjectId | None, *, what: str = "record") -> ObjectId:
    """Convert a path/form parameter to an ObjectId or raise 404.

    Callers used to do bare ``ObjectId(x)``, which raises ``InvalidId`` and
    surfaced as a 500 on any malformed URL.
    """
    if isinstance(value, ObjectId):
        return value
    if not value:
        raise NotFoundError(f"No such {what}.")
    try:
        return ObjectId(value)
    except (InvalidId, TypeError) as exc:
        raise NotFoundError(f"No such {what}.") from exc


def require_owner(document: dict[str, Any] | None, user_id: ObjectId, *, what: str = "record"):
    """Return ``document`` only if it exists and belongs to ``user_id``.

    A missing document and someone else's document both raise ``NotFoundError``
    so the response cannot be used to probe which ids exist.
    """
    if document is None:
        raise NotFoundError(f"No such {what}.")
    owner = document.get("user_id")
    if owner != user_id:
        raise NotFoundError(f"No such {what}.")
    return document


def ensure_admin() -> None:
    """Imperative guard for places a decorator doesn't fit."""
    if not current_user.is_authenticated or getattr(current_user, "role", None) != "admin":
        raise PermissionDeniedError()


# --------------------------------------------------------------------------
# Response headers
# --------------------------------------------------------------------------
_CSP = "; ".join(
    [
        "default-src 'self'",
        # Bootstrap + Font Awesome come from CDNs; inline handlers are avoided
        # but Bootstrap's own bundle needs 'unsafe-inline' for its styles.
        "script-src 'self' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com",
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com "
        "https://fonts.googleapis.com",
        "font-src 'self' https://fonts.gstatic.com https://cdnjs.cloudflare.com data:",
        # Plant photos may be served from an S3 presigned URL on any host.
        "img-src 'self' data: https:",
        "connect-src 'self'",
        "frame-ancestors 'none'",
        "base-uri 'self'",
        "form-action 'self'",
    ]
)


def init_app(app: Flask) -> None:
    @app.after_request
    def _security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
        )
        response.headers.setdefault("Content-Security-Policy", _CSP)
        if app.config.get("SESSION_COOKIE_SECURE"):
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response
