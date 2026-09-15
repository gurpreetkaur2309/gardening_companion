"""The dashboard: what needs attention, and everything growing right now."""

from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from ..errors import AppError
from ..forms import AddPlantForm, CareActionForm, ConfirmForm, GardenForm
from ..models import CareAction
from ..repositories import care_log_repo, plant_repo
from ..security import parse_object_id
from ..services import care_service, garden_service, storage_service

dashboard_bp = Blueprint("dashboard", __name__, url_prefix="/dashboard")


@dashboard_bp.route("/")
@login_required
def index():
    user_id = current_user.id
    garden, gardens = garden_service.resolve_active_garden(
        user_id, request.args.get("garden_id")
    )

    plantings = garden_service.list_plantings(garden.id, user_id)
    buckets = care_service.group_by_urgency(plantings)

    return render_template(
        "dashboard/index.html",
        garden=garden,
        gardens=gardens,
        buckets=buckets,
        summary=care_service.user_summary(user_id),
        week=care_service.upcoming_week(user_id),
        recent_activity=care_log_repo.list_for_user(user_id, limit=8),
        image_url=storage_service.image_url,
        add_form=AddPlantForm(garden_id=str(garden.id)),
        garden_form=GardenForm(),
        care_form=CareActionForm(),
        confirm_form=ConfirmForm(),
        catalog=plant_repo.list_choices(),
        CareAction=CareAction,
    )


@dashboard_bp.route("/plant/<planting_id>")
@login_required
def planting_detail(planting_id: str):
    pid = parse_object_id(planting_id, what="plant")
    planting = garden_service.get_planting(pid, current_user.id)
    return render_template(
        "dashboard/planting.html",
        planting=planting,
        history=care_log_repo.list_for_planting(pid, limit=40),
        pending=care_service.pending_actions(planting),
        image_url=storage_service.image_url,
        care_form=CareActionForm(),
        confirm_form=ConfirmForm(),
        CareAction=CareAction,
    )


@dashboard_bp.route("/plant/<planting_id>/care", methods=["POST"])
@login_required
def log_care(planting_id: str):
    """Record a care action from the dashboard buttons."""
    pid = parse_object_id(planting_id, what="plant")
    form = CareActionForm()
    if not form.validate_on_submit():
        flash("That action couldn't be recorded. Please try again.", "danger")
        return redirect(request.referrer or url_for("dashboard.index"))

    try:
        planting = care_service.record_care(
            pid, current_user.id, form.action.data, note=form.note.data
        )
    except AppError as exc:
        flash(exc.message, "danger")
    else:
        action = CareAction(form.action.data)
        next_due = (
            planting.next_water_due
            if action is CareAction.WATER
            else planting.next_fertilize_due
        )
        if next_due and action in CareAction.scheduled():
            flash(
                f"{action.label} {planting.display_name}. "
                f"Next due {next_due.strftime('%d %b')}.",
                "success",
            )
        else:
            flash(f"{action.label} {planting.display_name}.", "success")

    return redirect(request.referrer or url_for("dashboard.index"))


@dashboard_bp.route("/plant/<planting_id>/snooze", methods=["POST"])
@login_required
def snooze(planting_id: str):
    pid = parse_object_id(planting_id, what="plant")
    form = ConfirmForm()
    if not form.validate_on_submit():
        flash("That couldn't be snoozed. Please try again.", "danger")
        return redirect(request.referrer or url_for("dashboard.index"))

    action = request.form.get("action", CareAction.WATER)
    try:
        days = int(request.form.get("days", 1))
    except (TypeError, ValueError):
        days = 1

    try:
        care_service.snooze(pid, current_user.id, action, days)
    except AppError as exc:
        flash(exc.message, "danger")
    else:
        flash(f"Snoozed for {days} day{'s' if days != 1 else ''}.", "info")
    return redirect(request.referrer or url_for("dashboard.index"))
