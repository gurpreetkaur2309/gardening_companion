"""User-facing plant requests (asking for a species the catalog lacks)."""

from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, url_for
from flask_login import current_user, login_required

from ..errors import AppError
from ..extensions import limiter
from ..forms import RequestPlantForm
from ..repositories import plant_request_repo
from ..services import catalog_service

plants_bp = Blueprint("plants", __name__, url_prefix="/plants")


@plants_bp.route("/request", methods=["GET", "POST"])
@login_required
@limiter.limit("10 per hour", methods=["POST"])
def request_plant():
    form = RequestPlantForm()
    if form.validate_on_submit():
        try:
            catalog_service.submit_request(
                current_user.id, form.plant_name.data, form.description.data
            )
        except AppError as exc:
            flash(exc.message, "warning")
        else:
            flash(
                "Request submitted. An admin will review it and add the plant to "
                "the catalog.",
                "success",
            )
            return redirect(url_for("plants.request_plant"))

    return render_template(
        "plants/request.html",
        form=form,
        mine=plant_request_repo.list_for_user(current_user.id, limit=20),
    )
