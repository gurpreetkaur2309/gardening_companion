"""Background tasks (Celery).

Tasks are declared with ``@shared_task`` so importing them never requires a
Celery app to exist. ``celery_worker.py`` at the project root binds them to one.
"""

from .celery_app import create_celery

__all__ = ["create_celery"]
