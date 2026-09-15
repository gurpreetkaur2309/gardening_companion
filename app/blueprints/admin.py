"""Admin console: catalog curation, the request queue and user management.

Every route in this blueprint is gated by ``admin_required`` via a blueprint-wide
``before_request``, so a new route cannot accidentally ship unprotected.  In the
app this replaces, the equivalent endpoints (``/delete_user/<id>``,
``/update_user_role/<id>``, ``/delete_plant/<id>``) had no check at all.
"""

from __future__ import annotations

import logging

from flask import (
    Blueprint,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user

from ..errors import AppError
from ..forms import ConfirmForm, PlantForm, RejectRequestForm
from ..models import RequestStatus, UserRole, UserStatus
from ..repositories import (
    garden_plant_repo,
    plant_repo,
    plant_request_repo,
    user_repo,
)
from ..security import admin_required, parse_object_id
from ..services import catalog_service, garden_service, storage_service

logger = logging.getLogger(__name__)

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


@admin_bp.before_request
@admin_required
def _guard():
    """Blanket authorization for the whole blueprint."""


@admin_bp.app_context_processor
def _inject_pending_count() -> dict[str, int]:
    """Badge count in the nav — only computed for admins."""
    if current_user.is_authenticated and getattr(current_user, "is_admin", False):
        try:
            return {"pending_request_count": plant_request_repo.count_pending()}
        except Exception:
            return {"pending_request_count": 0}
    return {"pending_request_count": 0}


# ---------------------------------------------------------------------------
@admin_bp.route("/")
def index():
    return render_template(
        "admin/index.html",
        stats={
            "users": user_repo.count(),
            "admins": user_repo.count_admins(),
            "plants": plant_repo.count(),
            "plantings": garden_plant_repo.count(),
            "pending_requests": plant_request_repo.count_pending(),
        },
        recent_users=user_repo.list_all(limit=8),
        pending=catalog_service.list_requests()[:5],
    )


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------
@admin_bp.route("/plants")
def plants():
    query = request.args.get("q", "").strip()
    return render_template(
        "admin/plants.html",
        plants=plant_repo.list_all(search=query or None, limit=300),
        query=query,
        confirm_form=ConfirmForm(),
        image_url=storage_service.image_url,
    )


@admin_bp.route("/plants/new", methods=["GET", "POST"])
def create_plant():
    form = PlantForm()
    # Prefilled when an admin arrives here from the approve-request button.
    request_id = request.args.get("request_id")
    if request.method == "GET":
        form.common_name.data = request.args.get("common_name", "")
        form.description.data = request.args.get("description", "")

    if form.validate_on_submit():
        try:
            if request_id:
                _, plant = catalog_service.approve_request(
                    parse_object_id(request_id, what="request"),
                    current_user.id,
                    _plant_fields(form),
                    image=form.image.data or None,
                )
                flash(f"Approved and added '{plant.common_name}' to the catalog.", "success")
                return redirect(url_for("admin.requests"))

            plant = catalog_service.create_plant(
                _plant_fields(form), image=form.image.data or None
            )
        except AppError as exc:
            flash(exc.message, "danger")
        else:
            flash(f"Added '{plant.common_name}' to the catalog.", "success")
            return redirect(url_for("admin.plants"))

    return render_template(
        "admin/plant_form.html", form=form, plant=None, request_id=request_id
    )


@admin_bp.route("/plants/<plant_id>/edit", methods=["GET", "POST"])
def edit_plant(plant_id: str):
    pid = parse_object_id(plant_id, what="plant")
    plant = catalog_service.get_plant(pid)

    form = PlantForm()
    if request.method == "GET":
        form.common_name.data = plant.common_name
        form.scientific_name.data = plant.scientific_name
        form.family.data = plant.family
        form.genus.data = plant.genus
        form.edible.data = plant.edible
        form.description.data = plant.description
        form.sapling_description.data = plant.sapling_description
        form.watering_days.data = plant.care.watering_days
        form.fertilizing_days.data = plant.care.fertilizing_days
        form.sunlight.data = str(plant.care.sunlight)
        form.fertilizer_type.data = plant.care.fertilizer_type
        form.care_notes.data = plant.care.notes

    if form.validate_on_submit():
        try:
            catalog_service.update_plant(
                pid, _plant_fields(form), image=form.image.data or None
            )
        except AppError as exc:
            flash(exc.message, "danger")
        else:
            flash("Catalog entry updated.", "success")
            return redirect(url_for("admin.plants"))

    return render_template(
        "admin/plant_form.html",
        form=form,
        plant=plant,
        in_use=garden_plant_repo.count_using_plant(pid),
        image_url=storage_service.image_url,
    )


@admin_bp.route("/plants/<plant_id>/delete", methods=["POST"])
def delete_plant(plant_id: str):
    pid = parse_object_id(plant_id, what="plant")
    form = ConfirmForm()
    if not form.validate_on_submit():
        abort(400)

    try:
        catalog_service.delete_plant(pid, force=request.form.get("force") == "1")
    except AppError as exc:
        flash(exc.message, "danger")
    else:
        flash("Catalog entry deleted.", "info")
    return redirect(url_for("admin.plants"))


# ---------------------------------------------------------------------------
# Plant requests
# ---------------------------------------------------------------------------
@admin_bp.route("/requests")
def requests():
    status = request.args.get("status", RequestStatus.PENDING)
    if status not in set(RequestStatus):
        status = RequestStatus.PENDING
    return render_template(
        "admin/requests.html",
        requests=catalog_service.list_requests(status),
        status=status,
        reject_form=RejectRequestForm(),
        statuses=list(RequestStatus),
    )


@admin_bp.route("/requests/<request_id>/reject", methods=["POST"])
def reject_request(request_id: str):
    rid = parse_object_id(request_id, what="request")
    form = RejectRequestForm()
    if not form.validate_on_submit():
        abort(400)

    try:
        catalog_service.reject_request(rid, current_user.id, form.note.data)
    except AppError as exc:
        flash(exc.message, "danger")
    else:
        flash("Request rejected.", "info")
    return redirect(url_for("admin.requests"))


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
@admin_bp.route("/users")
def users():
    query = request.args.get("q", "").strip()
    return render_template(
        "admin/users.html",
        users=user_repo.list_all(search=query or None, limit=200),
        query=query,
        confirm_form=ConfirmForm(),
        roles=list(UserRole),
        statuses=list(UserStatus),
    )


@admin_bp.route("/users/<user_id>/role", methods=["POST"])
def set_role(user_id: str):
    uid = parse_object_id(user_id, what="user")
    form = ConfirmForm()
    if not form.validate_on_submit():
        abort(400)

    role = request.form.get("role", "")
    if role not in set(UserRole):
        flash("Unknown role.", "danger")
        return redirect(url_for("admin.users"))

    target = user_repo.get(uid)
    if target is None:
        flash("No such user.", "danger")
        return redirect(url_for("admin.users"))

    # Guard rails: don't strip your own admin rights, and never leave the
    # instance with zero admins — both are unrecoverable from the UI.
    if target.id == current_user.id and role != UserRole.ADMIN:
        flash("You can't remove your own admin access.", "warning")
        return redirect(url_for("admin.users"))
    if (
        target.role == UserRole.ADMIN
        and role != UserRole.ADMIN
        and user_repo.count_admins() <= 1
    ):
        flash("This is the last admin account. Promote someone else first.", "warning")
        return redirect(url_for("admin.users"))

    user_repo.set_role(uid, role)
    logger.info("Admin %s set role of %s to %s", current_user.username, target.username, role)
    flash(f"{target.display_name} is now {role}.", "success")
    return redirect(url_for("admin.users"))


@admin_bp.route("/users/<user_id>/status", methods=["POST"])
def set_status(user_id: str):
    uid = parse_object_id(user_id, what="user")
    form = ConfirmForm()
    if not form.validate_on_submit():
        abort(400)

    status = request.form.get("status", "")
    if status not in set(UserStatus):
        flash("Unknown status.", "danger")
        return redirect(url_for("admin.users"))

    target = user_repo.get(uid)
    if target is None:
        flash("No such user.", "danger")
        return redirect(url_for("admin.users"))
    if target.id == current_user.id:
        flash("You can't suspend your own account.", "warning")
        return redirect(url_for("admin.users"))

    user_repo.set_status(uid, status)
    flash(f"{target.display_name} is now {status}.", "success")
    return redirect(url_for("admin.users"))


@admin_bp.route("/users/<user_id>/delete", methods=["POST"])
def delete_user(user_id: str):
    uid = parse_object_id(user_id, what="user")
    form = ConfirmForm()
    if not form.validate_on_submit():
        abort(400)

    target = user_repo.get(uid)
    if target is None:
        flash("No such user.", "danger")
        return redirect(url_for("admin.users"))
    if target.id == current_user.id:
        flash("You can't delete your own account from here.", "warning")
        return redirect(url_for("admin.users"))
    if target.role == UserRole.ADMIN and user_repo.count_admins() <= 1:
        flash("This is the last admin account.", "warning")
        return redirect(url_for("admin.users"))

    # Cascade: an account's gardens, plantings and logs go with it.
    removed = garden_service.purge_user_data(uid)
    plant_request_repo.delete_all_for_user(uid)
    user_repo.delete(uid)

    logger.warning(
        "Admin %s deleted user %s (%s)", current_user.username, target.username, removed
    )
    flash(f"Deleted {target.display_name} and all of their data.", "info")
    return redirect(url_for("admin.users"))


# ---------------------------------------------------------------------------
@admin_bp.route("/reminders/preview")
def reminders_preview():
    """What the next reminder sweep would send. Read-only; sends nothing."""
    from ..tasks.reminders import collect_due_tasks

    by_user = collect_due_tasks()

    detail = []
    for uid, tasks in by_user.items():
        # A user deleted between the sweep and this call leaves orphan tasks;
        # fall back to the id rather than dereferencing None.
        owner = user_repo.get(uid)
        detail.append(
            {
                "user": owner.email if owner else str(uid),
                "tasks": [
                    {
                        "plant": t["plant_name"],
                        "action": t["action"],
                        "days_overdue": t["days_overdue"],
                    }
                    for t in tasks
                ],
            }
        )

    return jsonify(
        users=len(by_user),
        tasks=sum(len(v) for v in by_user.values()),
        detail=detail,
    )


def _plant_fields(form: PlantForm) -> dict:
    return {
        "common_name": form.common_name.data,
        "scientific_name": form.scientific_name.data,
        "family": form.family.data,
        "genus": form.genus.data,
        "edible": form.edible.data,
        "description": form.description.data,
        "sapling_description": form.sapling_description.data,
        "watering_days": form.watering_days.data,
        "fertilizing_days": form.fertilizing_days.data,
        "sunlight": form.sunlight.data,
        "fertilizer_type": form.fertilizer_type.data,
        "care_notes": form.care_notes.data,
        "review_note": form.review_note.data,
    }
