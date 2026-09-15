"""Celery wiring.

Every task runs inside a Flask application context, so tasks use the same
config, database and mail setup as the web process.  ``celery_app`` is also
importable at module scope (``celery -A app.tasks.celery_app:celery worker``)
because the worker entry point has no Flask app to hand it.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from celery import Celery, Task
from celery.signals import setup_logging
from flask import Flask, has_app_context

logger = logging.getLogger(__name__)


def create_celery(app: Flask) -> Celery:
    """Build a Celery app bound to a Flask app."""

    class FlaskTask(Task):
        """Run every task inside a Flask application context.

        Reuses a context if one is already pushed — otherwise calling a task
        directly (from a test, or from ``manage.py``) would silently run it
        against a second application rather than the caller's.
        """

        def __call__(self, *args, **kwargs):
            if has_app_context():
                return self.run(*args, **kwargs)
            with app.app_context():
                return self.run(*args, **kwargs)

    celery = Celery(app.import_name, task_cls=FlaskTask)
    sweep_minutes = app.config.get("REMINDER_SWEEP_MINUTES", 60)

    celery.conf.update(
        broker_url=app.config["CELERY_BROKER_URL"],
        result_backend=app.config["CELERY_RESULT_BACKEND"],
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
        # A worker that dies mid-task should hand the job back to the broker.
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        task_time_limit=300,
        task_soft_time_limit=270,
        result_expires=timedelta(days=1),
        broker_connection_retry_on_startup=True,
        # The schedule is static and declared here, not mutated at runtime.  The
        # previous implementation tried to append to ``beat_schedule`` on a
        # freshly constructed local Celery object per plant, which never
        # reached the running beat process and so never fired.
        beat_schedule={
            "sweep-care-reminders": {
                "task": "tasks.sweep_care_reminders",
                "schedule": timedelta(minutes=sweep_minutes),
            },
        },
    )

    celery.set_default()
    app.extensions["celery"] = celery
    return celery


@setup_logging.connect
def _keep_our_logging(**_kwargs):
    """Stop Celery from replacing the app's logging configuration."""
    return True


# NOTE: no Celery app is built at import time. Constructing one here would call
# create_app() — opening a real Mongo connection — merely because something
# imported a task module. The worker entry point is ``celery_worker.py`` at the
# project root, which builds both explicitly:
#
#     celery -A celery_worker:celery worker
#     celery -A celery_worker:celery beat
