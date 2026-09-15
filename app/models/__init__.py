"""Domain models.

Plain dataclasses with explicit ``from_doc`` / ``to_doc`` conversion.  Nothing
here touches Flask or PyMongo, so the rules can be unit-tested directly.
"""

from .care_log import CareAction, CareLog
from .enums import AgeStage, RequestStatus, Sunlight, UserRole, UserStatus
from .garden import Garden
from .garden_plant import CareSchedule, GardenPlant
from .plant import Plant
from .plant_request import PlantRequest
from .user import User

__all__ = [
    "AgeStage",
    "CareAction",
    "CareLog",
    "CareSchedule",
    "Garden",
    "GardenPlant",
    "Plant",
    "PlantRequest",
    "RequestStatus",
    "Sunlight",
    "User",
    "UserRole",
    "UserStatus",
]
