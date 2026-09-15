"""Repositories — the only place that talks to MongoDB.

Module-level instances resolve their database lazily from the app context, so
importing them at module scope is safe.  Tests build their own bound to a test
database: ``UserRepository(test_db)``.
"""

from .care_log_repo import CareLogRepository
from .chat_repo import ChatRepository
from .garden_plant_repo import GardenPlantRepository
from .garden_repo import GardenRepository
from .plant_repo import PlantRepository
from .plant_request_repo import PlantRequestRepository
from .reminder_repo import ReminderRepository
from .user_repo import UserRepository

user_repo = UserRepository()
plant_repo = PlantRepository()
garden_repo = GardenRepository()
garden_plant_repo = GardenPlantRepository()
care_log_repo = CareLogRepository()
plant_request_repo = PlantRequestRepository()
chat_repo = ChatRepository()
reminder_repo = ReminderRepository()

__all__ = [
    "CareLogRepository",
    "ChatRepository",
    "GardenPlantRepository",
    "GardenRepository",
    "PlantRepository",
    "PlantRequestRepository",
    "ReminderRepository",
    "UserRepository",
    "care_log_repo",
    "chat_repo",
    "garden_plant_repo",
    "garden_repo",
    "plant_repo",
    "plant_request_repo",
    "reminder_repo",
    "user_repo",
]
