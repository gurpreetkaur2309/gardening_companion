"""Configuration objects, one per environment.

Everything is driven by environment variables so the same image can run in dev,
CI and production.  ``ProductionConfig`` fails fast at boot if a required secret
is missing rather than silently running with a dev default.
"""

from __future__ import annotations

import os
from typing import Any


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


class BaseConfig:
    """Settings shared by every environment."""

    # --- Core ---
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-insecure-key")
    APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:5000")
    TESTING = False
    DEBUG = False

    # --- Mongo ---
    MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/gardening_companion")
    MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "gardening_companion")
    MONGO_SERVER_SELECTION_TIMEOUT_MS = _int("MONGO_SERVER_SELECTION_TIMEOUT_MS", 5000)

    # --- Sessions & cookies ---
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = False  # flipped on in production
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 24 * 14  # 14 days
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"

    # --- CSRF ---
    WTF_CSRF_TIME_LIMIT = None  # tie CSRF lifetime to the session, not a fixed clock

    # --- Uploads ---
    MAX_UPLOAD_MB = _int("MAX_UPLOAD_MB", 8)
    MAX_CONTENT_LENGTH = MAX_UPLOAD_MB * 1024 * 1024

    # --- Mail ---
    MAIL_SERVER = os.getenv("MAIL_SERVER", "localhost")
    MAIL_PORT = _int("MAIL_PORT", 1025)
    MAIL_USE_TLS = _bool("MAIL_USE_TLS", False)
    MAIL_USE_SSL = _bool("MAIL_USE_SSL", False)
    MAIL_USERNAME = os.getenv("MAIL_USERNAME") or None
    MAIL_PASSWORD = os.getenv("MAIL_PASSWORD") or None
    MAIL_DEFAULT_SENDER = os.getenv(
        "MAIL_DEFAULT_SENDER", "Gardening Companion <no-reply@gardening-companion.local>"
    )
    MAIL_SUPPRESS_SEND = False

    # --- Celery ---
    CELERY_BROKER_URL = os.getenv(
        "CELERY_BROKER_URL", os.getenv("REDIS_URL", "redis://localhost:6379/0")
    )
    CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")
    REMINDER_SWEEP_MINUTES = _int("REMINDER_SWEEP_MINUTES", 60)

    # --- Gemini ---
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or None
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-lite-latest")
    GEMINI_TIMEOUT_SECONDS = _int("GEMINI_TIMEOUT_SECONDS", 30)

    # --- Storage / S3 ---
    STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "auto")  # auto | s3 | local
    AWS_S3_BUCKET = os.getenv("AWS_S3_BUCKET") or None
    AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
    AWS_S3_ENDPOINT_URL = os.getenv("AWS_S3_ENDPOINT_URL") or None
    S3_PRESIGN_EXPIRY_SECONDS = _int("S3_PRESIGN_EXPIRY_SECONDS", 3600)
    LOCAL_UPLOAD_DIR = os.getenv("LOCAL_UPLOAD_DIR", "instance/uploads")

    # --- Google OAuth (optional) ---
    GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID") or None
    GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET") or None

    # --- Rate limiting ---
    RATELIMIT_STORAGE_URI = os.getenv("RATELIMIT_STORAGE_URI", "memory://")
    RATELIMIT_ENABLED = True

    # --- Logging ---
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    LOG_JSON = _bool("LOG_JSON", False)

    @classmethod
    def validate(cls) -> None:
        """Hook for environment-specific sanity checks. Base does nothing."""


class DevelopmentConfig(BaseConfig):
    DEBUG = True
    LOG_LEVEL = os.getenv("LOG_LEVEL", "DEBUG")


class TestingConfig(BaseConfig):
    TESTING = True
    # "strong" session protection keys the session to the request fingerprint,
    # which the test client can't reproduce when it sets _user_id directly.
    SESSION_PROTECTION = None
    DEBUG = False
    SECRET_KEY = "testing-key"
    MONGO_DB_NAME = os.getenv("MONGO_DB_NAME_TEST", "gardening_companion_test")
    WTF_CSRF_ENABLED = False
    MAIL_SUPPRESS_SEND = True
    RATELIMIT_ENABLED = False
    STORAGE_BACKEND = "local"
    LOCAL_UPLOAD_DIR = os.getenv("LOCAL_UPLOAD_DIR_TEST", "instance/test-uploads")
    GEMINI_API_KEY = None  # tests inject a fake client


class ProductionConfig(BaseConfig):
    SESSION_COOKIE_SECURE = True
    REMEMBER_COOKIE_SECURE = True
    PREFERRED_URL_SCHEME = "https"

    @classmethod
    def validate(cls) -> None:
        missing = []
        if not cls.SECRET_KEY or cls.SECRET_KEY == "dev-only-insecure-key":
            missing.append("SECRET_KEY")
        if not cls.MONGO_URI:
            missing.append("MONGO_URI")
        if missing:
            raise RuntimeError(
                "Refusing to start in production without: " + ", ".join(missing)
            )


_CONFIGS: dict[str, type[BaseConfig]] = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}


def get_config(name: str | None = None) -> type[BaseConfig]:
    """Resolve a config class from a name or the ``FLASK_ENV`` variable."""
    key = (name or os.getenv("FLASK_ENV") or "development").strip().lower()
    return _CONFIGS.get(key, DevelopmentConfig)


def as_dict(config: type[BaseConfig]) -> dict[str, Any]:
    """Uppercase attributes only — what ``app.config.from_object`` would take."""
    return {k: getattr(config, k) for k in dir(config) if k.isupper()}
