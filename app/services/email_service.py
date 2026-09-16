"""Transactional email over SMTP.

Every send goes through :func:`send` so a failing mail server degrades into a
logged warning instead of a 500 — a password reset request that can't send mail
should still render a page, and a reminder sweep that can't reach SMTP should
not lose the whole batch.
"""

from __future__ import annotations

import logging
from typing import Any

from flask import current_app, render_template
from flask_mail import Message

from ..extensions import mail

logger = logging.getLogger(__name__)


def send(
    *,
    subject: str,
    recipient: str,
    template: str,
    context: dict[str, Any] | None = None,
    sender: str | None = None,
) -> bool:
    """Render ``template`` as .html/.txt and send. Returns success."""
    ctx = {"base_url": current_app.config.get("APP_BASE_URL", ""), **(context or {})}

    try:
        html = render_template(f"email/{template}.html", **ctx)
    except Exception:
        logger.exception("Email template email/%s.html failed to render", template)
        return False

    try:
        text = render_template(f"email/{template}.txt", **ctx)
    except Exception:
        text = None

    message = Message(
        subject=subject,
        recipients=[recipient],
        html=html,
        body=text,
        sender=sender or current_app.config.get("MAIL_DEFAULT_SENDER"),
    )

    try:
        mail.send(message)
    except Exception:
        logger.error("Could not send '%s' to %s", subject, recipient, exc_info=True)
        return False

    logger.info("Sent '%s' to %s", subject, recipient)
    return True


# ---------------------------------------------------------------------------
# Concrete messages
# ---------------------------------------------------------------------------
def send_password_reset(user: Any, reset_url: str) -> bool:
    return send(
        subject="Reset your Gardening Companion password",
        recipient=user.email,
        template="password_reset",
        context={"user": user, "reset_url": reset_url},
    )


def send_welcome(user: Any) -> bool:
    return send(
        subject="Welcome to Gardening Companion 🌱",
        recipient=user.email,
        template="welcome",
        context={"user": user},
    )


def send_care_digest(user: Any, tasks: list[dict[str, Any]]) -> bool:
    """One digest per user per sweep, rather than an email per plant.

    Batching is what keeps this usable: someone with twenty plants coming due on
    the same morning gets one message, not twenty.
    """
    if not tasks:
        return False

    water = [t for t in tasks if t["action"] == "water"]
    fertilize = [t for t in tasks if t["action"] == "fertilize"]
    overdue = [t for t in tasks if t["days_overdue"] > 0]

    if len(tasks) == 1:
        # "needs watering", not "needs watered" — action_label is past tense.
        subject = f"🌱 {tasks[0]['plant_name']} needs {tasks[0]['action_gerund']}"
    elif overdue:
        subject = f"🌱 {len(tasks)} plants need attention ({len(overdue)} overdue)"
    else:
        subject = f"🌱 {len(tasks)} plants need attention today"

    return send(
        subject=subject,
        recipient=user.email,
        template="care_digest",
        context={
            "user": user,
            "tasks": tasks,
            "water": water,
            "fertilize": fertilize,
            "overdue": overdue,
        },
    )
