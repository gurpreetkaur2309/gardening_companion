"""Read-mostly JSON API (v1), session-authenticated.

Enough surface for a future mobile client or a dashboard widget.  Mutating
endpoints still require the CSRF token (sent as ``X-CSRFToken``) because auth is
cookie-based — exempting them would reintroduce CSRF through the back door.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from ..errors import AppError
from ..models import CareAction
from ..repositories import care_log_repo, garden_plant_repo, plant_repo
from ..security import parse_object_id
from ..services import care_service, garden_service, storage_service

api_bp = Blueprint("api", __name__, url_prefix="/api/v1")


@api_bp.route("/me")
@login_required
def me():
    return jsonify(
        user=current_user.to_public_dict(),
        summary=care_service.user_summary(current_user.id),
    )


@api_bp.route("/gardens")
@login_required
def gardens():
    counts = garden_plant_repo.count_by_garden(current_user.id)
    return jsonify(
        gardens=[
            g.to_public_dict(plant_count=counts.get(g.id, 0))
            for g in garden_service.list_gardens(current_user.id)
        ]
    )


@api_bp.route("/gardens/<garden_id>/plants")
@login_required
def garden_plants(garden_id: str):
    gid = parse_object_id(garden_id, what="garden")
    plantings = garden_service.list_plantings(gid, current_user.id)
    return jsonify(
        plants=[
            p.to_public_dict(
                image_url=storage_service.image_url(p.plant.image_key if p.plant else None)
            )
            for p in plantings
        ]
    )


@api_bp.route("/plants/due")
@login_required
def due():
    """Everything needing attention, grouped the way the dashboard shows it."""
    plantings = care_service.hydrate(garden_plant_repo.list_for_user(current_user.id))
    buckets = care_service.group_by_urgency(plantings)
    return jsonify(
        overdue=[p.to_public_dict() for p in buckets.overdue],
        due_today=[p.to_public_dict() for p in buckets.due_today],
        upcoming=[p.to_public_dict() for p in buckets.upcoming],
        counts={
            "overdue": len(buckets.overdue),
            "due_today": len(buckets.due_today),
            "upcoming": len(buckets.upcoming),
        },
    )


@api_bp.route("/plants/<planting_id>")
@login_required
def planting(planting_id: str):
    pid = parse_object_id(planting_id, what="plant")
    item = garden_service.get_planting(pid, current_user.id)
    return jsonify(
        plant=item.to_public_dict(
            image_url=storage_service.image_url(item.plant.image_key if item.plant else None)
        ),
        history=[log.to_public_dict() for log in care_log_repo.list_for_planting(pid, limit=25)],
    )


@api_bp.route("/plants/<planting_id>/care", methods=["POST"])
@login_required
def log_care(planting_id: str):
    pid = parse_object_id(planting_id, what="plant")
    payload = request.get_json(silent=True) or {}
    action = payload.get("action", CareAction.WATER)

    try:
        item = care_service.record_care(
            pid, current_user.id, action, note=payload.get("note")
        )
    except AppError as exc:
        return jsonify(error=exc.message), exc.status_code

    return jsonify(plant=item.to_public_dict())


@api_bp.route("/catalog")
@login_required
def catalog():
    query = request.args.get("q", "").strip()
    try:
        limit = min(int(request.args.get("limit", 50)), 200)
    except (TypeError, ValueError):
        limit = 50
    plants = plant_repo.list_all(search=query or None, limit=limit)
    return jsonify(
        plants=[
            p.to_public_dict(image_url=storage_service.image_url(p.image_key)) for p in plants
        ],
        count=len(plants),
    )


@api_bp.route("/week")
@login_required
def week():
    days = care_service.upcoming_week(current_user.id)
    return jsonify(
        week=[
            {
                "date": d["date"].isoformat(),
                "is_today": d["is_today"],
                "water": [p.display_name for p in d["water"]],
                "fertilize": [p.display_name for p in d["fertilize"]],
                "total": d["total"],
            }
            for d in days
        ]
    )
