"""Enrich: fill care schedule, edibility and sapling guidance with Gemini.

Scraped sources say what a plant *is*, not how to keep it alive.  This stage asks
Gemini for those fields with a JSON response schema, then treats the answer as
untrusted: it is validated against hard numeric bounds, low-confidence answers
are discarded (the record keeps no care and the app falls back to its one-time
lookup), and every accepted answer is tagged with model + prompt version so the
provenance survives into the catalog.

Responses are cached on disk by (model, prompt version, species), so re-runs and
resumed runs cost nothing.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

from .extract import Rejected

logger = logging.getLogger(__name__)

PROMPT_VERSION = "v2"
SUNLIGHT = {"full", "partial", "shade"}
CONFIDENCE = ("low", "medium", "high")
RELEVANCE = ("common", "niche", "not_cultivated")

SYSTEM_PROMPT = """You are a horticulture reference writing entries for a plant-care app.
Given a plant, return its baseline care for a healthy MATURE specimen grown in
a temperate climate in average potting soil or garden soil.

Rules:
- watering_days and fertilizing_days are whole numbers of days BETWEEN
  applications, never a range or a frequency. Watered twice a week -> 3.
- sunlight is exactly one of the following, defined as the app defines them:
    full    = 6+ hours of direct sun (outdoor sun-lovers, bright south windows)
    partial = 3-6 hours of sun, or bright light with some direct sun
    shade   = indirect or low light, no direct sun (typical for foliage
              houseplants such as snake plants, ZZ plants, ferns, calatheas)
- fertilizer_type is a short product description, e.g. "Balanced 10-10-10".
- care_notes is ONE short sentence naming the most common way people kill it.
- sapling_description is two short sentences on how to plant and start a young
  plant of this species.
- edible is true only if a part of this plant is commonly eaten by people.
- relevance: "common" if a home gardener or houseplant owner would plausibly
  grow it; "niche" if only specialists would; "not_cultivated" if it is
  essentially a wild plant nobody grows at home.
- confidence: how sure you are of the numbers. Use "low" if you are guessing
  from the genus rather than knowing this species. Never inflate it."""


class Enrichment(BaseModel):
    """The exact shape the model is asked to return."""

    watering_days: int = Field(ge=1, le=120)
    fertilizing_days: int = Field(ge=3, le=365)
    sunlight: str
    fertilizer_type: str = Field(min_length=3, max_length=80)
    care_notes: str = Field(max_length=300)
    sapling_description: str = Field(min_length=20, max_length=500)
    edible: bool
    relevance: str
    confidence: str

    @field_validator("sunlight", "relevance", "confidence", mode="before")
    @classmethod
    def _lower(cls, v: Any) -> Any:
        return v.strip().lower() if isinstance(v, str) else v

    @field_validator("sunlight")
    @classmethod
    def _sunlight(cls, v: str) -> str:
        if v not in SUNLIGHT:
            raise ValueError(f"sunlight must be one of {sorted(SUNLIGHT)}")
        return v

    @field_validator("relevance")
    @classmethod
    def _relevance(cls, v: str) -> str:
        if v not in RELEVANCE:
            raise ValueError(f"relevance must be one of {RELEVANCE}")
        return v

    @field_validator("confidence")
    @classmethod
    def _confidence(cls, v: str) -> str:
        if v not in CONFIDENCE:
            raise ValueError(f"confidence must be one of {CONFIDENCE}")
        return v


def make_client() -> Any | None:
    """A standalone Gemini client built from the environment (no Flask app)."""
    from dotenv import load_dotenv

    load_dotenv()
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        return None
    from app.services.gemini_service import GeminiClient

    return GeminiClient(
        api_key=key,
        model=os.getenv("GEMINI_MODEL", "gemini-flash-lite-latest"),
        timeout_seconds=int(os.getenv("GEMINI_TIMEOUT_SECONDS", "30")),
    )


class BudgetExhaustedError(Exception):
    """The per-run API call budget is spent; remaining species wait for the next run."""


class Enricher:
    """Calls Gemini politely: a shared requests-per-minute limit and a call budget.

    The free tier allows ~15 requests/minute and a daily cap, so a big catalog is
    enriched incrementally: the disk cache means each run picks up where the
    last one stopped.
    """

    def __init__(
        self,
        client: Any,
        cache_dir: Path,
        *,
        attempts: int = 3,
        backoff: float = 2.0,
        rpm: float = 12.0,
        max_calls: int | None = None,
    ):
        self.client = client
        self.min_interval = 60.0 / rpm if rpm else 0.0
        self.max_calls = max_calls
        self._lock = threading.Lock()
        self._next_slot = 0.0
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.attempts = attempts
        self.backoff = backoff
        self.calls = 0
        self.cache_hits = 0

    def _path(self, scientific_name: str) -> Path:
        key = f"{self.client.model}|{PROMPT_VERSION}|{scientific_name.lower()}"
        return self.cache_dir / f"{hashlib.sha1(key.encode()).hexdigest()}.json"

    def _reserve_slot(self) -> None:
        """Block until this call may go out; raise if the budget is spent."""
        with self._lock:
            if self.max_calls is not None and self.calls >= self.max_calls:
                raise BudgetExhaustedError
            self.calls += 1
            now = time.monotonic()
            slot = max(now, self._next_slot)
            self._next_slot = slot + self.min_interval
        if slot > now:
            time.sleep(slot - now)

    def enrich(self, common_name: str, scientific_name: str) -> Enrichment | None:
        """Return a validated enrichment, or ``None`` (not cached) on failure."""
        path = self._path(scientific_name)
        if path.exists():
            self.cache_hits += 1
            return Enrichment.model_validate_json(path.read_text())

        prompt = f"Plant: {common_name} ({scientific_name})"
        for attempt in range(self.attempts):
            self._reserve_slot()
            raw = self.client.generate(
                prompt,
                system_instruction=SYSTEM_PROMPT,
                temperature=0.2,
                max_output_tokens=600,
                response_schema=Enrichment,
            )
            if raw:
                try:
                    result = Enrichment.model_validate_json(raw)
                except ValidationError as exc:
                    # The model answered but broke a bound; retrying rarely helps.
                    logger.warning(
                        "%s: invalid enrichment (%s)", scientific_name, exc.errors()[0]["msg"]
                    )
                    return None
                path.write_text(result.model_dump_json())
                return result
            time.sleep(self.backoff * (attempt + 1))  # rate limit / transient failure
        return None


def apply_enrichment(row: dict[str, Any], e: Enrichment, *, model: str) -> dict[str, Any]:
    """Merge an accepted enrichment into a transformed row (returns a new dict)."""
    notes = e.care_notes.strip() or None
    return {
        **row,
        "edible": e.edible,
        "sapling_description": e.sapling_description.strip(),
        "care": {
            "watering_days": e.watering_days,
            "fertilizing_days": e.fertilizing_days,
            "sunlight": e.sunlight,
            "fertilizer_type": e.fertilizer_type.strip(),
            "notes": notes,
        },
        "care_source": "ai",
        "provenance": {
            **row.get("provenance", {}),
            "enrichment": {
                "model": model,
                "prompt_version": PROMPT_VERSION,
                "confidence": e.confidence,
                "relevance": e.relevance,
            },
        },
    }


def enrich_rows(
    enricher: Enricher, rows: list[dict[str, Any]], *, workers: int = 4
) -> tuple[list[dict[str, Any]], list[Rejected]]:
    """Enrich every row. Rows the model calls ``not_cultivated`` are dropped;
    failed or low-confidence rows are kept *without* care so nothing is guessed."""
    rejected: list[Rejected] = []

    def one(row: dict[str, Any]) -> tuple[dict[str, Any] | None, Rejected | None]:
        name = row["scientific_name"]
        try:
            e = enricher.enrich(row["common_name"], name)
        except BudgetExhaustedError:
            return row, Rejected(name, "enrich", "deferred_call_budget_kept_without_care")
        if e is None:
            return row, Rejected(name, "enrich", "no_ai_answer_kept_without_care")
        if e.relevance == "not_cultivated":
            return None, Rejected(name, "enrich", "not_cultivated")
        if e.confidence == "low":
            return row, Rejected(name, "enrich", "low_confidence_kept_without_care")
        return apply_enrichment(row, e, model=enricher.client.model), None

    out: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for kept, reject in pool.map(one, rows):
            if reject:
                rejected.append(reject)
            if kept is not None:
                out.append(kept)
    return out, rejected


# --- accuracy check against the hand-curated catalog -------------------------
def compare_to_curated(
    enricher: Enricher, curated: list[dict[str, Any]], *, tolerance: float = 0.5, workers: int = 4
) -> dict[str, Any]:
    """Run enrichment on species with hand-checked care and measure agreement.

    A watering/fertilising interval "agrees" when it is within ``tolerance`` of
    the curated value (relative), since e.g. 5 vs 7 days is the same practical
    advice.  Sunlight must match exactly.
    """

    def one(rec: dict[str, Any]) -> dict[str, Any]:
        try:
            e = enricher.enrich(rec["common_name"], rec["scientific_name"])
        except BudgetExhaustedError:
            e = None
        if e is None:
            return {"name": rec["scientific_name"], "answered": False}
        care = rec["care"]

        def close(a: int, b: int) -> bool:
            return abs(a - b) <= tolerance * b

        return {
            "name": rec["scientific_name"],
            "answered": True,
            "confidence": e.confidence,
            "water": [e.watering_days, care["watering_days"]],
            "water_ok": close(e.watering_days, care["watering_days"]),
            "fert": [e.fertilizing_days, care["fertilizing_days"]],
            "fert_ok": close(e.fertilizing_days, care["fertilizing_days"]),
            "sun": [e.sunlight, care["sunlight"]],
            "sun_ok": e.sunlight == care["sunlight"],
            "edible": [e.edible, rec.get("edible", False)],
            "edible_ok": e.edible == bool(rec.get("edible", False)),
        }

    with ThreadPoolExecutor(max_workers=workers) as pool:
        rows = list(pool.map(one, curated))
    answered = [r for r in rows if r["answered"]]
    n = len(answered) or 1
    return {
        "model": enricher.client.model,
        "prompt_version": PROMPT_VERSION,
        "species": len(rows),
        "answered": len(answered),
        "watering_within_tolerance": round(sum(r["water_ok"] for r in answered) / n, 3),
        "fertilizing_within_tolerance": round(sum(r["fert_ok"] for r in answered) / n, 3),
        "sunlight_exact": round(sum(r["sun_ok"] for r in answered) / n, 3),
        "edible_exact": round(sum(r["edible_ok"] for r in answered) / n, 3),
        "tolerance": tolerance,
        "rows": rows,
    }


def load_curated(seed_path: Path) -> list[dict[str, Any]]:
    return [r for r in json.loads(seed_path.read_text()) if r.get("care")]
