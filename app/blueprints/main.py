"""Public pages, health checks and locally-stored media."""

from __future__ import annotations

import logging

from flask import Blueprint, Response, abort, redirect, render_template, url_for
from flask_login import current_user

from ..db import ping
from ..repositories import plant_repo
from ..services import gemini_service
from ..services.storage_service import LocalStorage, get_storage

logger = logging.getLogger(__name__)

main_bp = Blueprint("main", __name__)
media_bp = Blueprint("media", __name__, url_prefix="/media")


@main_bp.route("/")
def home():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    # The species count is decorative. The public landing page is the one thing
    # that should still render when the database is unreachable, so a failed
    # count hides the line rather than taking the page down.
    try:
        catalog_size = plant_repo.count()
    except Exception:
        logger.warning("Catalog count unavailable for the landing page")
        catalog_size = None

    return render_template("home.html", catalog_size=catalog_size)


@main_bp.route("/healthz")
def healthz():
    """Liveness + dependency probe for load balancers and compose healthchecks."""
    db_ok = ping()
    payload = {
        "status": "ok" if db_ok else "degraded",
        "database": "up" if db_ok else "down",
        "ai": "enabled" if gemini_service.is_available() else "disabled",
        "storage": get_storage().name,
    }
    return payload, (200 if db_ok else 503)


@media_bp.route("/<path:key>")
def serve_upload(key: str):
    """Serve an image from the local storage backend.

    Only registered when storage is local; with S3 the browser fetches the
    object directly from a presigned URL and never touches this route.
    """
    storage = get_storage()
    if not isinstance(storage, LocalStorage):
        abort(404)
    try:
        data, content_type = storage.open(key)
    except (FileNotFoundError, Exception):
        abort(404)
    return Response(
        data,
        mimetype=content_type,
        headers={"Cache-Control": "public, max-age=604800"},
    )
