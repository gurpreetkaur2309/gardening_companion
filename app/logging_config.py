"""Logging setup.

Human-readable lines in development; one JSON object per line in production so
CloudWatch / Loki can index the fields.  Every record carries a request id,
which is also returned to the client in the ``X-Request-ID`` header.
"""

from __future__ import annotations

import json
import logging
import sys
import uuid
from typing import Any

from flask import Flask, g, has_request_context, request


class RequestContextFilter(logging.Filter):
    """Attach request-scoped fields to every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        if has_request_context():
            record.request_id = getattr(g, "request_id", "-")
            record.method = request.method
            record.path = request.path
            record.remote_addr = request.remote_addr or "-"
        else:
            record.request_id = "-"
            record.method = "-"
            record.path = "-"
            record.remote_addr = "-"
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
            "method": getattr(record, "method", "-"),
            "path": getattr(record, "path", "-"),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def init_app(app: Flask) -> None:
    level = getattr(logging, str(app.config.get("LOG_LEVEL", "INFO")).upper(), logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RequestContextFilter())
    if app.config.get("LOG_JSON"):
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)-7s [%(request_id)s] %(name)s: %(message)s",
                datefmt="%H:%M:%S",
            )
        )

    root = logging.getLogger()
    # Replace handlers so reloads don't stack duplicates.
    root.handlers = [handler]
    root.setLevel(level)

    # These are chatty and rarely useful at INFO.
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    logging.getLogger("botocore").setLevel(logging.WARNING)
    logging.getLogger("boto3").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("pymongo").setLevel(logging.WARNING)

    # Flask's logger propagates to root, which already has the handler above.
    # Giving it a handler of its own as well would print every app.logger line
    # twice, so it gets a level and nothing else.
    app.logger.handlers = []
    app.logger.propagate = True
    app.logger.setLevel(level)

    @app.before_request
    def _assign_request_id() -> None:
        g.request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]

    @app.after_request
    def _echo_request_id(response):
        response.headers["X-Request-ID"] = getattr(g, "request_id", "-")
        return response
