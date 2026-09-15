"""Account settings."""

from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, url_for
from flask_login import current_user, login_required

from ..errors import AppError
from ..forms import ChangePasswordForm, ProfileForm
from ..repositories import care_log_repo, plant_request_repo
from ..services import auth_service, care_service

profile_bp = Blueprint("profile", __name__, url_prefix="/profile")


@profile_bp.route("/", methods=["GET", "POST"])
@login_required
def index():
    form = ProfileForm(obj=current_user)
    password_form = ChangePasswordForm()

    if form.submit.data and form.validate_on_submit():
        try:
            auth_service.update_profile(
                current_user.id,
                {
                    "full_name": form.full_name.data,
                    "username": form.username.data,
                    "phone": form.phone.data,
                },
            )
        except AppError as exc:
            flash(exc.message, "danger")
        else:
            flash("Profile updated.", "success")
            return redirect(url_for("profile.index"))

    return render_template(
        "profile.html",
        form=form,
        password_form=password_form,
        summary=care_service.user_summary(current_user.id),
        recent=care_log_repo.list_for_user(current_user.id, limit=15),
        requests=plant_request_repo.list_for_user(current_user.id, limit=10),
    )


@profile_bp.route("/password", methods=["POST"])
@login_required
def change_password():
    form = ChangePasswordForm()
    if not form.validate_on_submit():
        for errors in form.errors.values():
            flash(errors[0], "danger")
        return redirect(url_for("profile.index"))

    try:
        auth_service.change_password(
            current_user.id, form.current_password.data, form.password.data
        )
    except AppError as exc:
        flash(exc.message, "danger")
    else:
        flash("Password changed.", "success")
    return redirect(url_for("profile.index"))
