"""Flask-WTF forms.

Using WTForms rather than hand-rolled ``request.form.get`` checks buys three
things at once: CSRF protection on every POST, server-side validation that
can't be bypassed by disabling the browser's, and field errors rendered next to
the input that caused them.

Business rules (uniqueness, password strength) stay in the service layer; these
classes only cover shape and presence.
"""

from __future__ import annotations

from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField
from wtforms import (
    BooleanField,
    HiddenField,
    IntegerField,
    PasswordField,
    SelectField,
    StringField,
    SubmitField,
    TextAreaField,
)
from wtforms.validators import (
    DataRequired,
    Email,
    EqualTo,
    Length,
    NumberRange,
    Optional,
    Regexp,
)

from .models import AgeStage, Sunlight
from .services.auth_service import MIN_PASSWORD_LENGTH

IMAGE_EXTENSIONS = ["jpg", "jpeg", "png", "webp", "gif", "bmp"]

# Syntax only. The default also resolves MX records, which puts a live DNS
# lookup on the critical path of every signup — slow, and it fails the whole
# form when the resolver is unreachable. Whether the address actually receives
# mail is answered by the confirmation email, not by the form.
_LENGTH_MESSAGE = f"At least {MIN_PASSWORD_LENGTH} characters."

_email = Email("That isn't a valid email address.", check_deliverability=False)

USERNAME_PATTERN = r"^[A-Za-z][A-Za-z0-9_]{3,29}$"
USERNAME_MESSAGE = "Start with a letter; 4-30 characters of letters, numbers or underscores."

PHONE_PATTERN = r"^\+?[0-9]{10,15}$"
PHONE_MESSAGE = "10-15 digits, optionally starting with +."


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
class LoginForm(FlaskForm):
    identifier = StringField(
        "Email, username or phone",
        validators=[DataRequired("Enter your email, username or phone number."), Length(max=160)],
    )
    password = PasswordField("Password", validators=[DataRequired("Enter your password.")])
    remember = BooleanField("Keep me signed in")
    submit = SubmitField("Sign in")


class SignupForm(FlaskForm):
    full_name = StringField(
        "Full name",
        validators=[DataRequired("Enter your name."), Length(min=2, max=80)],
    )
    username = StringField(
        "Username",
        validators=[
            DataRequired("Choose a username."),
            Regexp(USERNAME_PATTERN, message=USERNAME_MESSAGE),
        ],
    )
    email = StringField(
        "Email",
        validators=[DataRequired("Enter your email."), _email, Length(max=160)],
    )
    phone = StringField(
        "Phone (optional)",
        validators=[Optional(), Regexp(PHONE_PATTERN, message=PHONE_MESSAGE)],
    )
    password = PasswordField(
        "Password",
        validators=[
            DataRequired("Choose a password."),
            Length(min=MIN_PASSWORD_LENGTH, message=_LENGTH_MESSAGE),
        ],
    )
    confirm = PasswordField(
        "Confirm password",
        validators=[
            DataRequired("Repeat your password."),
            EqualTo("password", "Passwords don't match."),
        ],
    )
    submit = SubmitField("Create account")


class ForgotPasswordForm(FlaskForm):
    email = StringField(
        "Email",
        validators=[DataRequired("Enter your email."), _email],
    )
    submit = SubmitField("Send reset link")


class ResetPasswordForm(FlaskForm):
    password = PasswordField(
        "New password",
        validators=[
            DataRequired("Choose a new password."),
            Length(min=MIN_PASSWORD_LENGTH, message=_LENGTH_MESSAGE),
        ],
    )
    confirm = PasswordField(
        "Confirm new password",
        validators=[
            DataRequired("Repeat your new password."),
            EqualTo("password", "Passwords don't match."),
        ],
    )
    submit = SubmitField("Update password")


class ChangePasswordForm(FlaskForm):
    current_password = PasswordField("Current password", validators=[Optional()])
    password = PasswordField(
        "New password",
        validators=[
            DataRequired("Choose a new password."),
            Length(min=MIN_PASSWORD_LENGTH, message=_LENGTH_MESSAGE),
        ],
    )
    confirm = PasswordField(
        "Confirm new password",
        validators=[
            DataRequired("Repeat your new password."),
            EqualTo("password", "Passwords don't match."),
        ],
    )
    submit = SubmitField("Change password")


class ProfileForm(FlaskForm):
    full_name = StringField("Full name", validators=[DataRequired(), Length(min=2, max=80)])
    username = StringField(
        "Username",
        validators=[DataRequired(), Regexp(USERNAME_PATTERN, message=USERNAME_MESSAGE)],
    )
    phone = StringField(
        "Phone",
        validators=[Optional(), Regexp(PHONE_PATTERN, message=PHONE_MESSAGE)],
    )
    submit = SubmitField("Save changes")


# ---------------------------------------------------------------------------
# Gardens & plantings
# ---------------------------------------------------------------------------
class GardenForm(FlaskForm):
    name = StringField(
        "Garden name",
        validators=[DataRequired("Give your garden a name."), Length(max=60)],
    )
    location = StringField("Location (optional)", validators=[Optional(), Length(max=80)])
    description = TextAreaField("Description (optional)", validators=[Optional(), Length(max=500)])
    submit = SubmitField("Create garden")


class AddPlantForm(FlaskForm):
    plant_id = HiddenField(validators=[DataRequired("Pick a plant.")])
    garden_id = HiddenField(validators=[DataRequired()])
    nickname = StringField("Nickname (optional)", validators=[Optional(), Length(max=60)])
    age_stage = SelectField(
        "Life stage",
        choices=[(str(s), s.label) for s in AgeStage],
        default=str(AgeStage.MATURE),
    )
    quantity = IntegerField(
        "How many?",
        default=1,
        validators=[DataRequired(), NumberRange(min=1, max=999, message="Between 1 and 999.")],
    )
    submit = SubmitField("Add to garden")


class EditPlantingForm(FlaskForm):
    nickname = StringField("Nickname", validators=[Optional(), Length(max=60)])
    quantity = IntegerField("Quantity", validators=[DataRequired(), NumberRange(min=1, max=999)])
    age_stage = SelectField("Life stage", choices=[(str(s), s.label) for s in AgeStage])
    watering_days = IntegerField(
        "Water every (days)",
        validators=[Optional(), NumberRange(min=1, max=120, message="Between 1 and 120 days.")],
    )
    fertilizing_days = IntegerField(
        "Fertilize every (days)",
        validators=[Optional(), NumberRange(min=3, max=365, message="Between 3 and 365 days.")],
    )
    fertilizer_type = StringField("Fertilizer", validators=[Optional(), Length(max=80)])
    sunlight = SelectField(
        "Sunlight", choices=[(str(s), s.label) for s in Sunlight], validators=[Optional()]
    )
    reminders_enabled = BooleanField("Email me reminders for this plant")
    notes = TextAreaField("Notes", validators=[Optional(), Length(max=2000)])
    submit = SubmitField("Save")


class CareActionForm(FlaskForm):
    """Tiny form behind the Water / Fertilize buttons — exists for CSRF."""

    action = HiddenField(validators=[DataRequired()])
    note = StringField("Note", validators=[Optional(), Length(max=280)])
    submit = SubmitField("Done")


class RequestPlantForm(FlaskForm):
    plant_name = StringField(
        "Plant name",
        validators=[DataRequired("Which plant should we add?"), Length(max=120)],
    )
    description = TextAreaField(
        "Anything else we should know?", validators=[Optional(), Length(max=2000)]
    )
    submit = SubmitField("Submit request")


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------
class PlantForm(FlaskForm):
    common_name = StringField("Common name", validators=[DataRequired(), Length(max=120)])
    scientific_name = StringField("Scientific name", validators=[Optional(), Length(max=160)])
    family = StringField("Family", validators=[Optional(), Length(max=120)])
    genus = StringField("Genus", validators=[Optional(), Length(max=120)])
    edible = BooleanField("Edible")
    description = TextAreaField("Description", validators=[Optional(), Length(max=4000)])
    sapling_description = TextAreaField(
        "How to plant a sapling", validators=[Optional(), Length(max=4000)]
    )
    watering_days = IntegerField(
        "Water every (days)", validators=[Optional(), NumberRange(min=1, max=120)]
    )
    fertilizing_days = IntegerField(
        "Fertilize every (days)", validators=[Optional(), NumberRange(min=3, max=365)]
    )
    sunlight = SelectField(
        "Sunlight",
        choices=[(str(s), s.label) for s in Sunlight],
        default=str(Sunlight.PARTIAL),
    )
    fertilizer_type = StringField("Fertilizer", validators=[Optional(), Length(max=80)])
    care_notes = StringField("Care note", validators=[Optional(), Length(max=400)])
    image = FileField(
        "Photo",
        validators=[
            Optional(),
            FileAllowed(IMAGE_EXTENSIONS, "Images only (JPG, PNG, WebP, GIF)."),
        ],
    )
    review_note = HiddenField()
    submit = SubmitField("Save plant")


class RejectRequestForm(FlaskForm):
    note = StringField("Reason (optional)", validators=[Optional(), Length(max=280)])
    submit = SubmitField("Reject")


class ConfirmForm(FlaskForm):
    """Bare CSRF-carrying form for destructive POST buttons."""

    submit = SubmitField("Confirm")
