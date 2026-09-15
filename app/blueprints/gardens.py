"""Garden and planting management."""

from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from ..errors import AppError
from ..forms import AddPlantForm, ConfirmForm, EditPlantingForm, GardenForm
from ..models import AgeStage
from ..repositories import plant_repo
from ..security import parse_object_id
from ..services import garden_service, storage_service

gardens_bp = Blueprint("gardens", __name__, url_prefix="/gardens")


@gardens_bp.route("/", methods=["GET"])
@login_required
def index():
    from ..repositories import garden_plant_repo

    gardens = garden_service.list_gardens(current_user.id)
    return render_template(
        "gardens/index.html",
        gardens=gardens,
        counts=garden_plant_repo.count_by_garden(current_user.id),
        form=GardenForm(),
        confirm_form=ConfirmForm(),
    )


@gardens_bp.route("/create", methods=["POST"])
@login_required
def create():
    form = GardenForm()
    if not form.validate_on_submit():
        for errors in form.errors.values():
            flash(errors[0], "danger")
        return redirect(request.referrer or url_for("gardens.index"))

    try:
        garden = garden_service.create_garden(
            current_user.id,
            form.name.data,
            location=form.location.data,
            description=form.description.data,
        )
    except AppError as exc:
        flash(exc.message, "danger")
        return redirect(request.referrer or url_for("gardens.index"))

    flash(f"Created '{garden.name}'.", "success")
    return redirect(url_for("dashboard.index", garden_id=str(garden.id)))


@gardens_bp.route("/<garden_id>/edit", methods=["GET", "POST"])
@login_required
def edit(garden_id: str):
    gid = parse_object_id(garden_id, what="garden")
    garden = garden_service.get_garden(gid, current_user.id)

    form = GardenForm(obj=garden)
    form.submit.label.text = "Save changes"
    if form.validate_on_submit():
        try:
            garden_service.update_garden(
                gid,
                current_user.id,
                {
                    "name": form.name.data,
                    "location": form.location.data,
                    "description": form.description.data,
                },
            )
        except AppError as exc:
            flash(exc.message, "danger")
        else:
            flash("Garden updated.", "success")
            return redirect(url_for("dashboard.index", garden_id=garden_id))

    return render_template("gardens/edit.html", form=form, garden=garden)


@gardens_bp.route("/<garden_id>/delete", methods=["POST"])
@login_required
def delete(garden_id: str):
    gid = parse_object_id(garden_id, what="garden")
    form = ConfirmForm()
    if not form.validate_on_submit():
        flash("That couldn't be deleted. Please try again.", "danger")
        return redirect(url_for("gardens.index"))

    try:
        removed = garden_service.delete_garden(gid, current_user.id)
    except AppError as exc:
        flash(exc.message, "danger")
    else:
        flash(
            f"Garden deleted along with {removed} planting{'s' if removed != 1 else ''}.",
            "info",
        )
    return redirect(url_for("gardens.index"))


# ---------------------------------------------------------------------------
# Plantings
# ---------------------------------------------------------------------------
@gardens_bp.route("/plants/add", methods=["POST"])
@login_required
def add_plant():
    form = AddPlantForm()
    if not form.validate_on_submit():
        for errors in form.errors.values():
            flash(errors[0], "danger")
        return redirect(request.referrer or url_for("dashboard.index"))

    try:
        planting, created = garden_service.add_plant(
            user_id=current_user.id,
            garden_id=parse_object_id(form.garden_id.data, what="garden"),
            plant_id=parse_object_id(form.plant_id.data, what="plant"),
            age_stage=form.age_stage.data,
            quantity=form.quantity.data or 1,
            nickname=form.nickname.data,
        )
    except AppError as exc:
        flash(exc.message, "danger")
        return redirect(request.referrer or url_for("dashboard.index"))

    if created:
        due = planting.next_water_due
        flash(
            f"Added {planting.display_name}. Water every "
            f"{planting.schedule.watering_days} days — first due "
            f"{due.strftime('%d %b') if due else 'soon'}.",
            "success",
        )
    else:
        flash(
            f"You now have {planting.quantity} x {planting.display_name} in this garden.",
            "success",
        )
    return redirect(url_for("dashboard.index", garden_id=str(planting.garden_id)))


@gardens_bp.route("/plants/<planting_id>/edit", methods=["GET", "POST"])
@login_required
def edit_planting(planting_id: str):
    pid = parse_object_id(planting_id, what="plant")
    planting = garden_service.get_planting(pid, current_user.id)

    form = EditPlantingForm()
    if request.method == "GET":
        form.nickname.data = planting.nickname
        form.quantity.data = planting.quantity
        form.age_stage.data = str(planting.age_stage)
        form.watering_days.data = planting.schedule.watering_days
        form.fertilizing_days.data = planting.schedule.fertilizing_days
        form.fertilizer_type.data = planting.schedule.fertilizer_type
        form.sunlight.data = str(planting.schedule.sunlight)
        form.reminders_enabled.data = planting.reminders_enabled
        form.notes.data = planting.notes

    if form.validate_on_submit():
        try:
            garden_service.update_planting(
                pid,
                current_user.id,
                {
                    "nickname": form.nickname.data,
                    "quantity": form.quantity.data,
                    "age_stage": form.age_stage.data,
                    "watering_days": form.watering_days.data,
                    "fertilizing_days": form.fertilizing_days.data,
                    "fertilizer_type": form.fertilizer_type.data,
                    "sunlight": form.sunlight.data,
                    "reminders_enabled": form.reminders_enabled.data,
                    "notes": form.notes.data,
                },
            )
        except AppError as exc:
            flash(exc.message, "danger")
        else:
            flash("Plant updated.", "success")
            return redirect(url_for("dashboard.planting_detail", planting_id=planting_id))

    return render_template(
        "gardens/edit_planting.html",
        form=form,
        planting=planting,
        image_url=storage_service.image_url,
        AgeStage=AgeStage,
    )


@gardens_bp.route("/plants/<planting_id>/remove", methods=["POST"])
@login_required
def remove_planting(planting_id: str):
    pid = parse_object_id(planting_id, what="plant")
    form = ConfirmForm()
    if not form.validate_on_submit():
        flash("That couldn't be removed. Please try again.", "danger")
        return redirect(request.referrer or url_for("dashboard.index"))

    all_of_them = request.form.get("all") == "1"
    name = request.form.get("name") or "That plant"

    try:
        remaining = garden_service.remove_plant(pid, current_user.id, all_of_them=all_of_them)
    except AppError as exc:
        flash(exc.message, "danger")
        return redirect(request.referrer or url_for("dashboard.index"))

    if remaining is None:
        flash(f"{name} removed from your garden.", "info")
        return redirect(url_for("dashboard.index"))
    flash(f"{name}: {remaining} left.", "info")
    return redirect(request.referrer or url_for("dashboard.index"))


@gardens_bp.route("/plants/<planting_id>/move", methods=["POST"])
@login_required
def move_planting(planting_id: str):
    pid = parse_object_id(planting_id, what="plant")
    form = ConfirmForm()
    if not form.validate_on_submit():
        flash("That couldn't be moved. Please try again.", "danger")
        return redirect(request.referrer or url_for("dashboard.index"))

    target = parse_object_id(request.form.get("garden_id"), what="garden")
    try:
        planting = garden_service.move_planting(pid, current_user.id, target)
    except AppError as exc:
        flash(exc.message, "danger")
        return redirect(request.referrer or url_for("dashboard.index"))

    flash(f"Moved {planting.display_name}.", "success")
    return redirect(url_for("dashboard.index", garden_id=str(target)))


@gardens_bp.route("/catalog")
@login_required
def catalog():
    """Browse the shared species catalog and add straight from a card."""
    query = request.args.get("q", "").strip()
    plants = plant_repo.list_all(search=query or None, limit=200)
    garden, gardens = garden_service.resolve_active_garden(
        current_user.id, request.args.get("garden_id")
    )
    return render_template(
        "plants/catalog.html",
        plants=plants,
        query=query,
        garden=garden,
        gardens=gardens,
        add_form=AddPlantForm(garden_id=str(garden.id)),
        image_url=storage_service.image_url,
    )
