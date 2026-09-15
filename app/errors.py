"""Application exceptions and HTTP error handlers.

Services raise these; blueprints mostly don't catch them.  The handlers below
turn them into an HTML page or a JSON body depending on what the client asked
for, and never leak a stack trace to the user.
"""

from __future__ import annotations

import logging
import uuid

from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

logger = logging.getLogger(__name__)


class AppError(Exception):
    """Base class for expected, user-facing failures."""

    status_code = 400
    message = "Something went wrong."

    def __init__(self, message: str | None = None, *, status_code: int | None = None):
        super().__init__(message or self.message)
        if message:
            self.message = message
        if status_code:
            self.status_code = status_code


class ValidationError(AppError):
    status_code = 400
    message = "The submitted data is not valid."


class NotFoundError(AppError):
    status_code = 404
    message = "Not found."


class PermissionDeniedError(AppError):
    status_code = 403
    message = "You do not have permission to do that."


class ConflictError(AppError):
    status_code = 409
    message = "That conflicts with something that already exists."


class ExternalServiceError(AppError):
    """An upstream (Gemini, S3, SMTP) failed. Callers usually degrade instead."""

    status_code = 502
    message = "An external service is unavailable. Please try again shortly."


def _wants_json() -> bool:
    """True when the caller is an API client rather than a browser page load."""
    if request.path.startswith("/api/"):
        return True
    if request.is_json:
        return True
    accept = request.accept_mimetypes
    return accept.accept_json and not accept.accept_html


def init_app(app: Flask) -> None:
    @app.errorhandler(AppError)
    def _handle_app_error(exc: AppError):
        if _wants_json():
            return jsonify(error=exc.message, status=exc.status_code), exc.status_code
        return (
            render_template("errors/generic.html", code=exc.status_code, message=exc.message),
            exc.status_code,
        )

    @app.errorhandler(HTTPException)
    def _handle_http_error(exc: HTTPException):
        code = exc.code or 500
        message = exc.description or "Request failed."
        if _wants_json():
            return jsonify(error=message, status=code), code
        template = f"errors/{code}.html"
        try:
            return render_template(template, code=code, message=message), code
        except Exception:
            return render_template("errors/generic.html", code=code, message=message), code

    @app.errorhandler(Exception)
    def _handle_unexpected(exc: Exception):
        # Log with a correlation id the user can quote in a bug report, but show
        # them nothing about the internals.
        incident = uuid.uuid4().hex[:12]
        logger.exception("Unhandled exception [incident=%s] on %s", incident, request.path)
        message = f"An unexpected error occurred. Reference: {incident}"
        if _wants_json():
            return jsonify(error=message, status=500, incident=incident), 500
        return render_template("errors/500.html", code=500, message=message, incident=incident), 500
