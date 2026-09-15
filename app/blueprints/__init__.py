"""HTTP layer. Each module owns one blueprint and does no business logic."""

from .admin import admin_bp
from .api import api_bp
from .auth import auth_bp
from .chat import chat_bp
from .dashboard import dashboard_bp
from .gardens import gardens_bp
from .main import main_bp, media_bp
from .plants import plants_bp
from .profile import profile_bp

ALL_BLUEPRINTS = (
    main_bp,
    media_bp,
    auth_bp,
    dashboard_bp,
    gardens_bp,
    plants_bp,
    chat_bp,
    profile_bp,
    admin_bp,
    api_bp,
)

__all__ = ["ALL_BLUEPRINTS"]
