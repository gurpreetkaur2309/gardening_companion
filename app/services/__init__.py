"""Service layer: business logic, one module per concern.

Blueprints call services; services call repositories.  Nothing here renders a
template or reads ``request`` directly.
"""

from . import (
    auth_service,
    care_service,
    catalog_service,
    email_service,
    garden_service,
    gemini_service,
    storage_service,
)

__all__ = [
    "auth_service",
    "care_service",
    "catalog_service",
    "email_service",
    "garden_service",
    "gemini_service",
    "storage_service",
]
