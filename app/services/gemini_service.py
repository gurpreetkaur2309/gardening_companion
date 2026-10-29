"""Gemini integration: care-schedule inference and the garden-aware chatbot.

Two things this module refuses to do, both of which the earlier version of this
app got wrong:

1. **Parse care data out of prose.**  Schedules are requested with a JSON
   response schema and validated with Pydantic.  The old code string-split the
   model's free text, silently produced ``{}`` on any drift, and then raised a
   ``KeyError`` that surfaced to users as "Error adding plant".
2. **Be load-bearing.**  Every entry point degrades: no API key, a timeout, a
   refusal or a malformed answer all fall back to curated or conservative data.
   The app is fully usable with ``GEMINI_API_KEY`` unset.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from flask import Flask, current_app
from pydantic import BaseModel, Field
from pydantic import ValidationError as PydanticValidationError

from ..models.enums import CareSource, Sunlight
from ..models.plant import PlantCare

logger = logging.getLogger(__name__)

# Returned when the model is unavailable and no curated data exists.  Chosen to
# be conservative: watering too rarely is far more survivable than too often.
CONSERVATIVE_CARE = PlantCare(
    watering_days=7,
    fertilizing_days=30,
    sunlight=Sunlight.PARTIAL,
    fertilizer_type="Balanced 10-10-10",
    notes="Default schedule — adjust to suit your climate and soil.",
)

AI_UNAVAILABLE_REPLY = (
    "The AI assistant isn't configured right now, so I can't answer free-form "
    "questions. Your plants, schedules and reminders all still work normally. "
    "(An administrator can enable this by setting GEMINI_API_KEY.)"
)


# ---------------------------------------------------------------------------
# Structured output schema
# ---------------------------------------------------------------------------
class CareScheduleAnswer(BaseModel):
    """The exact shape the model is asked to return for a care lookup."""

    watering_days: int = Field(
        ge=1, le=120, description="Days between waterings for a mature plant."
    )
    fertilizing_days: int = Field(
        ge=3, le=365, description="Days between fertiliser applications."
    )
    sunlight: str = Field(description="One of: full, partial, shade.")
    fertilizer_type: str = Field(
        max_length=80, description="Recommended fertiliser, e.g. 'Balanced 10-10-10'."
    )
    notes: str = Field(
        default="", max_length=400, description="One short sentence of care advice."
    )

    def to_plant_care(self) -> PlantCare:
        sunlight = self.sunlight.strip().lower()
        if sunlight not in set(Sunlight):
            sunlight = Sunlight.PARTIAL
        return PlantCare(
            watering_days=self.watering_days,
            fertilizing_days=self.fertilizing_days,
            sunlight=sunlight,
            fertilizer_type=self.fertilizer_type.strip() or "Balanced 10-10-10",
            notes=self.notes.strip() or None,
        )


@dataclass
class CareLookupResult:
    care: PlantCare
    source: str  # CareSource value
    succeeded: bool


# ---------------------------------------------------------------------------
# Client wiring
# ---------------------------------------------------------------------------
class GeminiClient:
    """Thin wrapper over ``google-genai`` that never raises at the call site."""

    def __init__(self, api_key: str, model: str, timeout_seconds: int = 30) -> None:
        from google import genai  # imported lazily: optional dependency

        self.model = model
        self._genai = genai
        self._client = genai.Client(
            api_key=api_key,
            http_options={"timeout": timeout_seconds * 1000},  # SDK wants ms
        )

    # -- low level ---------------------------------------------------------
    def generate(
        self,
        contents: Any,
        *,
        system_instruction: str | None = None,
        temperature: float = 0.7,
        max_output_tokens: int = 800,
        response_schema: type[BaseModel] | None = None,
    ) -> str | None:
        """Return the model's text, or ``None`` if anything at all went wrong."""
        from google.genai import types

        config_args: dict[str, Any] = {
            "temperature": temperature,
            "max_output_tokens": max_output_tokens,
        }
        if system_instruction:
            config_args["system_instruction"] = system_instruction
        if response_schema is not None:
            config_args["response_mime_type"] = "application/json"
            config_args["response_schema"] = response_schema

        try:
            response = self._client.models.generate_content(
                model=self.model,
                contents=contents,
                config=types.GenerateContentConfig(**config_args),
            )
        except Exception:
            logger.warning("Gemini request failed", exc_info=True)
            return None

        text = getattr(response, "text", None)
        if not text:
            # Usually a safety block or an empty candidate list.
            logger.info("Gemini returned no text (blocked or empty response)")
            return None
        return text.strip()


def init_app(app: Flask) -> None:
    """Build the client once at boot, or record that AI is off."""
    api_key = app.config.get("GEMINI_API_KEY")
    if not api_key:
        app.extensions["gemini"] = None
        app.logger.info("Gemini: disabled (no GEMINI_API_KEY) — curated care data only")
        return
    try:
        app.extensions["gemini"] = GeminiClient(
            api_key=api_key,
            model=app.config.get("GEMINI_MODEL", "gemini-flash-lite-latest"),
            timeout_seconds=app.config.get("GEMINI_TIMEOUT_SECONDS", 30),
        )
        app.logger.info("Gemini: enabled (model=%s)", app.config.get("GEMINI_MODEL"))
    except Exception:
        app.extensions["gemini"] = None
        app.logger.warning("Gemini: could not initialise client, disabling", exc_info=True)


def get_client() -> GeminiClient | None:
    """The configured client, or ``None`` when AI features are off."""
    return current_app.extensions.get("gemini")


def is_available() -> bool:
    return get_client() is not None


# ---------------------------------------------------------------------------
# Feature 1: care-schedule inference
# ---------------------------------------------------------------------------
_CARE_SYSTEM_PROMPT = """You are a horticulture reference. Given a plant name, \
return its baseline care requirements for a healthy MATURE specimen grown in a \
temperate climate in average potting soil.

Rules:
- watering_days and fertilizing_days are whole numbers of days BETWEEN \
applications, not a frequency and not a range. If a plant is watered twice a \
week, watering_days is 3.
- sunlight must be exactly one of: full, partial, shade.
- fertilizer_type is a short product description such as "Balanced 10-10-10", \
"High-nitrogen 20-10-10" or "Low-phosphorus cactus feed".
- notes is one short sentence naming the single most common way people kill \
this plant.
- If the name is not a real plant, still return a safe generic houseplant \
schedule rather than refusing."""


def infer_care_schedule(plant_name: str, scientific_name: str | None = None) -> CareLookupResult:
    """Ask Gemini for a species' care schedule.

    Always returns a usable result. ``succeeded`` reports whether the numbers
    came from the model or from the conservative fallback, so callers can decide
    whether the answer is worth caching.
    """
    client = get_client()
    if client is None:
        return CareLookupResult(CONSERVATIVE_CARE, CareSource.DEFAULT, False)

    label = plant_name.strip()
    if scientific_name:
        label = f"{label} ({scientific_name.strip()})"

    raw = client.generate(
        f"Plant: {label}",
        system_instruction=_CARE_SYSTEM_PROMPT,
        temperature=0.2,  # this is a lookup, not a creative task
        max_output_tokens=400,
        response_schema=CareScheduleAnswer,
    )
    if not raw:
        return CareLookupResult(CONSERVATIVE_CARE, CareSource.DEFAULT, False)

    try:
        answer = CareScheduleAnswer.model_validate_json(raw)
    except PydanticValidationError:
        # Schema mode should prevent this, but a model can still emit JSON that
        # violates the numeric bounds. Try a plain parse before giving up.
        try:
            answer = CareScheduleAnswer.model_validate(json.loads(raw))
        except Exception:
            logger.warning("Gemini care answer failed validation for %r", label)
            return CareLookupResult(CONSERVATIVE_CARE, CareSource.DEFAULT, False)

    logger.info("Gemini supplied a care schedule for %r", label)
    return CareLookupResult(answer.to_plant_care(), CareSource.AI, True)


# ---------------------------------------------------------------------------
# Feature 2: the garden-aware chatbot
# ---------------------------------------------------------------------------
_CHAT_SYSTEM_PROMPT = """You are Sprout, the assistant inside a plant-care app \
called Gardening Companion. You help people keep their plants alive.

Voice: warm, brief, practical. Two or three short paragraphs at most. Use \
Markdown sparingly — bold for the key action, "- " bullets for steps. Never \
open with a greeting unless the user greeted you first.

Scope: plant care, diagnosis, soil, watering, light, pests, propagation, \
harvesting, and this app's own features. If asked something clearly unrelated, \
say in one friendly line that you only cover gardening, then stop.

You are given the user's actual garden below. Use it:
- Refer to their plants by name when relevant.
- When they ask what needs doing, answer from the overdue/due lists, not \
generically.
- Never invent a plant they do not have, and never claim to have changed \
anything in their account — you can advise, but the user performs actions in \
the app themselves.
- If their garden is empty, say so and suggest they add a plant first."""


def build_garden_context(
    *,
    display_name: str,
    gardens: list[str] | None = None,
    plants: list[dict[str, Any]] | None = None,
    overdue: list[str] | None = None,
    due_today: list[str] | None = None,
) -> str:
    """Render the user's garden into the compact block the prompt embeds.

    Kept as plain text rather than JSON — it costs fewer tokens and models
    follow it more reliably.
    """
    lines = [f"User: {display_name}"]

    if gardens:
        lines.append(f"Gardens: {', '.join(gardens)}")
    else:
        lines.append("Gardens: none yet")

    if plants:
        lines.append(f"Plants ({len(plants)}):")
        for p in plants[:40]:  # a long tail of plants is not worth the tokens
            bits = [f"- {p.get('name')}"]
            if p.get("species") and p.get("species") != p.get("name"):
                bits.append(f"({p['species']})")
            if p.get("quantity", 1) > 1:
                bits.append(f"x{p['quantity']}")
            if p.get("age_stage"):
                bits.append(f"[{p['age_stage']}]")
            if p.get("watering_days"):
                bits.append(f"water every {p['watering_days']}d")
            if p.get("garden"):
                bits.append(f"in {p['garden']}")
            lines.append(" ".join(bits))
        if len(plants) > 40:
            lines.append(f"- ...and {len(plants) - 40} more")
    else:
        lines.append("Plants: none added yet")

    lines.append(f"Overdue right now: {', '.join(overdue) if overdue else 'nothing'}")
    lines.append(f"Due today: {', '.join(due_today) if due_today else 'nothing'}")
    return "\n".join(lines)


def chat(
    message: str,
    *,
    history: list[dict[str, Any]] | None = None,
    garden_context: str = "",
    max_history: int = 12,
) -> str:
    """Answer a chat message. Never raises; returns a message either way."""
    client = get_client()
    if client is None:
        return AI_UNAVAILABLE_REPLY

    from google.genai import types

    contents: list[Any] = []
    for entry in (history or [])[-max_history:]:
        role = entry.get("role")
        text = (entry.get("content") or "").strip()
        if not text or role not in ("user", "model"):
            continue
        contents.append(types.Content(role=role, parts=[types.Part(text=text)]))
    contents.append(types.Content(role="user", parts=[types.Part(text=message.strip())]))

    system = _CHAT_SYSTEM_PROMPT
    if garden_context:
        system = f"{system}\n\n--- THE USER'S GARDEN ---\n{garden_context}"

    reply = client.generate(
        contents,
        system_instruction=system,
        temperature=0.7,
        max_output_tokens=700,
    )
    if not reply:
        return (
            "Sorry — I couldn't come up with an answer just then. "
            "Try rephrasing the question, or ask me again in a moment."
        )
    return reply


def suggest_plants(existing: list[str], *, conditions: str = "") -> list[str]:
    """Suggest companion species for a garden. Empty list when unavailable."""
    client = get_client()
    if client is None:
        return []

    class Suggestions(BaseModel):
        plants: list[str] = Field(max_length=6)

    have = ", ".join(existing[:25]) if existing else "nothing yet"
    prompt = (
        f"A gardener currently grows: {have}. "
        f"{'Conditions: ' + conditions + '. ' if conditions else ''}"
        "Suggest up to 5 more plants that suit the same conditions and care "
        "rhythm. Return common names only."
    )
    raw = client.generate(
        prompt,
        temperature=0.8,
        max_output_tokens=200,
        response_schema=Suggestions,
    )
    if not raw:
        return []
    try:
        return Suggestions.model_validate_json(raw).plants
    except Exception:
        return []
