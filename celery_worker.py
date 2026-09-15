"""Celery entry point.

    celery -A celery_worker:celery worker --loglevel=info
    celery -A celery_worker:celery beat   --loglevel=info

Kept out of the ``app`` package deliberately: building a Celery app opens a
Mongo connection through ``create_app()``, which should happen when a worker
starts, not whenever some module imports a task.
"""

from app import create_app
from app.tasks.celery_app import create_celery

flask_app = create_app()
celery = create_celery(flask_app)

# Import for the side effect of registering the tasks on this app.
from app.tasks import reminders  # noqa: E402,F401

__all__ = ["celery", "flask_app"]
