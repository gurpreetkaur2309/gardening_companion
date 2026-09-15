"""Application factory.

``create_app()`` is the single entry point used by the dev server, gunicorn,
the Celery worker and the test suite, so all four run identical wiring.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

from dotenv import load_dotenv
from flask import Flask

load_dotenv()

__version__ = "1.0.0"


def create_app(config_name: str | None = None) -> Flask:
    from . import db, errors, logging_config, security
    from .blueprints import ALL_BLUEPRINTS
    from .config import get_config
    from .extensions import csrf, limiter, login_manager, mail
    from .services import gemini_service, storage_service

    config = get_config(config_name)
    config.validate()

    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config)

    os.makedirs(app.instance_path, exist_ok=True)

    # Order matters: logging first so everything after it can report problems.
    logging_config.init_app(app)
    db.init_app(app)

    login_manager.init_app(app)
    if "SESSION_PROTECTION" in app.config:
        login_manager.session_protection = app.config["SESSION_PROTECTION"]
    mail.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)
    if not app.config.get("RATELIMIT_ENABLED", True):
        limiter.enabled = False

    storage_service.init_app(app)
    gemini_service.init_app(app)
    security.init_app(app)
    errors.init_app(app)

    _register_user_loader()
    _register_template_helpers(app)

    for blueprint in ALL_BLUEPRINTS:
        app.register_blueprint(blueprint)

    app.logger.info(
        "Gardening Companion %s started (env=%s, db=%s)",
        __version__,
        config.__name__,
        app.config["MONGO_DB_NAME"],
    )
    return app


def _register_user_loader() -> None:
    from bson import ObjectId
    from bson.errors import InvalidId

    from .extensions import login_manager
    from .repositories import user_repo

    @login_manager.user_loader
    def load_user(user_id: str):
        try:
            return user_repo.get(ObjectId(user_id))
        except (InvalidId, TypeError):
            return None


def _register_template_helpers(app: Flask) -> None:
    """Filters and globals the templates rely on."""
    from .models import AgeStage, CareAction, Sunlight
    from .services import gemini_service

    @app.template_filter("humandate")
    def humandate(value: datetime | None) -> str:
        """'Today', 'Tomorrow', '3 days ago', or a short date."""
        if value is None:
            return "—"
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        delta = (value.date() - datetime.now(UTC).date()).days
        if delta == 0:
            return "Today"
        if delta == 1:
            return "Tomorrow"
        if delta == -1:
            return "Yesterday"
        if 0 < delta <= 6:
            return f"in {delta} days"
        if -7 < delta < 0:
            return f"{abs(delta)} days ago"
        return value.strftime("%d %b %Y")

    @app.template_filter("timeago")
    def timeago(value: datetime | None) -> str:
        if value is None:
            return "—"
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        seconds = (datetime.now(UTC) - value).total_seconds()
        if seconds < 60:
            return "just now"
        if seconds < 3600:
            return f"{int(seconds // 60)}m ago"
        if seconds < 86400:
            return f"{int(seconds // 3600)}h ago"
        days = int(seconds // 86400)
        if days < 30:
            return f"{days}d ago"
        return value.strftime("%d %b %Y")

    @app.template_filter("pluralise")
    def pluralise(count: int, singular: str, plural: str | None = None) -> str:
        return f"{count} {singular if count == 1 else (plural or singular + 's')}"

    @app.context_processor
    def inject_globals() -> dict[str, object]:
        return {
            "AgeStage": AgeStage,
            "CareAction": CareAction,
            "Sunlight": Sunlight,
            "ai_enabled": gemini_service.is_available(),
            "app_version": __version__,
            "now": datetime.now(UTC),
        }


__all__ = ["__version__", "create_app"]
