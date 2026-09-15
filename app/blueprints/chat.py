"""The Sprout chatbot.

Answers arrive over ``fetch`` and render client-side.  The previous version
POSTed the form and redirected for every single message, so each question cost a
full page reload and lost the user's scroll position.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user, login_required

from ..extensions import limiter
from ..repositories import chat_repo
from ..repositories.chat_repo import ROLE_ASSISTANT, ROLE_USER
from ..services import care_service, gemini_service

logger = logging.getLogger(__name__)

chat_bp = Blueprint("chat", __name__, url_prefix="/chat")

MAX_MESSAGE_LENGTH = 1000
DAILY_MESSAGE_CAP = 100


@chat_bp.route("/")
@login_required
def index():
    return render_template(
        "chat.html",
        history=chat_repo.full_history(current_user.id, limit=100),
        ai_enabled=gemini_service.is_available(),
    )


@chat_bp.route("/history")
@login_required
def history():
    messages = chat_repo.history(current_user.id, limit=50)
    return jsonify(
        messages=[
            {
                "role": m["role"],
                "content": m["content"],
                "created_at": m["created_at"].isoformat(),
            }
            for m in messages
        ],
        ai_enabled=gemini_service.is_available(),
    )


@chat_bp.route("/send", methods=["POST"])
@login_required
@limiter.limit("30 per minute; 300 per hour")
def send():
    payload = request.get_json(silent=True) or {}
    message = (payload.get("message") or "").strip()

    if not message:
        return jsonify(error="Type a message first."), 400
    if len(message) > MAX_MESSAGE_LENGTH:
        return jsonify(error=f"Keep messages under {MAX_MESSAGE_LENGTH} characters."), 400

    # A per-user daily cap on top of the IP rate limit: the limiter protects the
    # server, this protects the API bill from one logged-in user.
    since = datetime.now(UTC) - timedelta(days=1)
    if chat_repo.count_since(current_user.id, since) >= DAILY_MESSAGE_CAP:
        return (
            jsonify(error="You've reached today's message limit. Try again tomorrow."),
            429,
        )

    history = chat_repo.history(current_user.id, limit=12)
    chat_repo.add(current_user.id, ROLE_USER, message)

    context = care_service.build_chat_context(current_user.id, current_user.display_name)
    reply = gemini_service.chat(
        message,
        history=[{"role": m["role"], "content": m["content"]} for m in history],
        garden_context=context,
    )

    chat_repo.add(current_user.id, ROLE_ASSISTANT, reply)
    return jsonify(reply=reply, ai_enabled=gemini_service.is_available())


@chat_bp.route("/clear", methods=["POST"])
@login_required
def clear():
    removed = chat_repo.clear(current_user.id)
    return jsonify(cleared=removed)


@chat_bp.route("/suggestions")
@login_required
def suggestions():
    """Companion-planting ideas based on what the user already grows."""
    from ..repositories import garden_plant_repo

    plantings = care_service.hydrate(garden_plant_repo.list_for_user(current_user.id))
    existing = [p.plant.common_name for p in plantings if p.plant]
    return jsonify(suggestions=gemini_service.suggest_plants(existing))
