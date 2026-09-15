"""Authentication routes: signup, login, password reset, Google sign-in."""

from __future__ import annotations

import logging
import secrets
from urllib.parse import urlparse

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user

from ..errors import AppError
from ..extensions import limiter
from ..forms import (
    ForgotPasswordForm,
    LoginForm,
    ResetPasswordForm,
    SignupForm,
)
from ..repositories import user_repo
from ..services import auth_service, email_service

logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


def _safe_next(target: str | None) -> str:
    """Only follow a ``next`` parameter that stays on this site.

    An open redirect here is a phishing primitive: attacker sends a link to the
    real login page that bounces to their lookalike after a successful sign-in.
    """
    if not target:
        return url_for("dashboard.index")
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc:
        return url_for("dashboard.index")
    if not target.startswith("/"):
        return url_for("dashboard.index")
    return target


def google_enabled() -> bool:
    return bool(
        current_app.config.get("GOOGLE_CLIENT_ID")
        and current_app.config.get("GOOGLE_CLIENT_SECRET")
    )


@auth_bp.app_context_processor
def _inject_google_flag() -> dict[str, object]:
    """Templates ask ``google_enabled`` before rendering the OAuth button."""
    return {"google_enabled": google_enabled()}


# ---------------------------------------------------------------------------
# Email + password
# ---------------------------------------------------------------------------
@auth_bp.route("/signup", methods=["GET", "POST"])
@limiter.limit("10 per hour; 40 per day", methods=["POST"])
def signup():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    form = SignupForm()
    if form.validate_on_submit():
        try:
            user = auth_service.register(
                full_name=form.full_name.data,
                username=form.username.data,
                email=form.email.data,
                password=form.password.data,
                phone=form.phone.data,
            )
        except AppError as exc:
            flash(exc.message, "danger")
        else:
            login_user(user, remember=True)
            _queue_welcome(user)
            flash(f"Welcome, {user.display_name}! Let's get your first plant in.", "success")
            return redirect(url_for("dashboard.index"))

    return render_template("auth/signup.html", form=form)


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("20 per hour; 100 per day", methods=["POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    form = LoginForm()
    if form.validate_on_submit():
        try:
            user = auth_service.authenticate(form.identifier.data, form.password.data)
        except AppError as exc:
            flash(exc.message, "danger")
        else:
            login_user(user, remember=bool(form.remember.data))
            logger.info("Login: %s", user.username)
            return redirect(_safe_next(request.args.get("next")))

    return render_template("auth/login.html", form=form)


@auth_bp.route("/logout")
@login_required
def logout():
    # Order matters. ``logout_user()`` records an instruction in the session
    # telling Flask-Login to delete the remember-me cookie on this response;
    # clearing the session afterwards would erase that instruction and leave the
    # cookie in place, silently signing the user straight back in.
    for key in ("oauth_state", "oauth_next"):
        session.pop(key, None)
    logout_user()
    flash("You've been signed out.", "info")
    return redirect(url_for("main.home"))


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
@limiter.limit("5 per hour; 20 per day", methods=["POST"])
def forgot_password():
    form = ForgotPasswordForm()
    if form.validate_on_submit():
        email = (form.email.data or "").strip().lower()
        user = user_repo.get_by_email(email)
        if user and user.is_active:
            token = auth_service.generate_reset_token(user, current_app.config["SECRET_KEY"])
            reset_url = url_for("auth.reset_password", token=token, _external=True)
            email_service.send_password_reset(user, reset_url)
            if current_app.debug:
                logger.info("Password reset link for %s: %s", email, reset_url)

        # Always the same response, whether or not the address is registered —
        # otherwise this form tells an attacker who has an account here.
        flash(
            "If that email is registered, a reset link is on its way. "
            "Check your spam folder if it doesn't arrive.",
            "info",
        )
        return redirect(url_for("auth.login"))

    return render_template("auth/forgot_password.html", form=form)


@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
@limiter.limit("10 per hour", methods=["POST"])
def reset_password(token: str):
    secret = current_app.config["SECRET_KEY"]

    try:
        auth_service.verify_reset_token(token, secret)
    except AppError as exc:
        flash(exc.message, "danger")
        return redirect(url_for("auth.forgot_password"))

    form = ResetPasswordForm()
    if form.validate_on_submit():
        try:
            auth_service.reset_password(token, form.password.data, secret)
        except AppError as exc:
            flash(exc.message, "danger")
        else:
            flash("Your password has been updated. Sign in with it now.", "success")
            return redirect(url_for("auth.login"))

    return render_template("auth/reset_password.html", form=form, token=token)


# ---------------------------------------------------------------------------
# Google OAuth 2.0 (only wired up when credentials are configured)
# ---------------------------------------------------------------------------
@auth_bp.route("/google")
@limiter.limit("20 per hour")
def google_login():
    if not google_enabled():
        flash("Google sign-in isn't configured on this instance.", "warning")
        return redirect(url_for("auth.login"))

    # `state` defends the callback against CSRF; `nonce` ties the id_token to
    # this specific authorization request.
    state = secrets.token_urlsafe(32)
    session["oauth_state"] = state
    session["oauth_next"] = request.args.get("next") or ""

    params = {
        "client_id": current_app.config["GOOGLE_CLIENT_ID"],
        "redirect_uri": url_for("auth.google_callback", _external=True),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    }
    from urllib.parse import urlencode

    return redirect(f"{GOOGLE_AUTH_URL}?{urlencode(params)}")


@auth_bp.route("/google/callback")
@limiter.limit("20 per hour")
def google_callback():
    import requests

    if not google_enabled():
        return redirect(url_for("auth.login"))

    expected_state = session.pop("oauth_state", None)
    next_target = session.pop("oauth_next", "")

    if request.args.get("error"):
        flash("Google sign-in was cancelled.", "info")
        return redirect(url_for("auth.login"))

    state = request.args.get("state")
    if not expected_state or not state or not secrets.compare_digest(state, expected_state):
        flash("Google sign-in failed a security check. Please try again.", "danger")
        return redirect(url_for("auth.login"))

    code = request.args.get("code")
    if not code:
        flash("Google sign-in didn't complete. Please try again.", "danger")
        return redirect(url_for("auth.login"))

    try:
        token_response = requests.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": current_app.config["GOOGLE_CLIENT_ID"],
                "client_secret": current_app.config["GOOGLE_CLIENT_SECRET"],
                "redirect_uri": url_for("auth.google_callback", _external=True),
                "grant_type": "authorization_code",
            },
            timeout=10,
        )
        token_response.raise_for_status()
        access_token = token_response.json().get("access_token")
        if not access_token:
            raise ValueError("no access_token in Google's response")

        profile_response = requests.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        profile_response.raise_for_status()
        profile = profile_response.json()
    except Exception:
        logger.warning("Google OAuth exchange failed", exc_info=True)
        flash("Couldn't complete Google sign-in. Please try again.", "danger")
        return redirect(url_for("auth.login"))

    try:
        user, created = auth_service.upsert_google_user(profile)
    except AppError as exc:
        flash(exc.message, "danger")
        return redirect(url_for("auth.login"))

    login_user(user, remember=True)
    if created:
        _queue_welcome(user)
        flash(f"Welcome, {user.display_name}! Your account is ready.", "success")
    else:
        flash(f"Welcome back, {user.display_name}.", "success")
    return redirect(_safe_next(next_target))


# ---------------------------------------------------------------------------
def _queue_welcome(user) -> None:
    """Send the welcome email through Celery, or inline if no broker is up."""
    try:
        from ..tasks.reminders import send_welcome_email

        send_welcome_email.delay(str(user.id))
    except Exception:
        logger.info("Celery unavailable; sending welcome email inline")
        try:
            email_service.send_welcome(user)
        except Exception:
            logger.warning("Welcome email failed", exc_info=True)
