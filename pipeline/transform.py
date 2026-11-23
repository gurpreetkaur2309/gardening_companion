"""Transform: map cleaned records onto the catalog's ``Plant`` shape."""

from __future__ import annotations

import re
from datetime import UTC, datetime

from .records import CleanRecord, PlantRecord

DESCRIPTION_MAX = 450
IMAGE_WIDTH = 500  # one of Wikimedia's standard thumbnail steps

_SENTENCE_END = re.compile(r"(?<=[a-z0-9\)])\.\s+(?=[A-Z])")
_THUMB_WIDTH = re.compile(r"/\d+px-")
_EDIBLE = re.compile(
    r"\b(edible|culinary|cooking|cuisine|eaten|vegetables?|spices?|seasoning|flavou?ring|"
    r"herbs?|crop|cultivated for (?:its|their) (?:fruits?|seeds?|leaves|roots?)|"
    r"fruits? (?:is|are) (?:eaten|used))\b",
    re.I,
)
_NOT_EDIBLE = re.compile(r"\b(poisonous|toxic|inedible|not edible|deadly)\b", re.I)


def shorten_description(text: str, limit: int = DESCRIPTION_MAX) -> str:
    """Whole sentences up to ``limit`` characters (always at least one)."""
    sentences = _SENTENCE_END.split(text)
    out = ""
    for i, sentence in enumerate(sentences):
        piece = sentence if sentence.endswith(".") or i == len(sentences) - 1 else sentence + "."
        candidate = f"{out} {piece}".strip()
        if out and len(candidate) > limit:
            break
        out = candidate
    if len(out) > limit:  # a single very long sentence
        out = out[: limit - 1].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return out


def is_edible(text: str) -> bool:
    """Keyword heuristic over the description. Toxicity wins over edibility."""
    return bool(_EDIBLE.search(text)) and not _NOT_EDIBLE.search(text)


def normalise_image(url: str | None) -> str | None:
    if not url:
        return None
    url = url.split("?", 1)[0].replace("http://", "https://")
    # The REST API hands back the thumb. host; upload. is the canonical one.
    url = url.replace("//thumb.wikimedia.org/", "//upload.wikimedia.org/")
    return _THUMB_WIDTH.sub(f"/{IMAGE_WIDTH}px-", url)


def to_plant_record(rec: CleanRecord, *, now: datetime | None = None) -> dict:  # type: ignore[type-arg]
    """Build the (unvalidated) dict for ``PlantRecord``; validation is its own stage."""
    return {
        "common_name": rec.common_name,
        "scientific_name": rec.scientific_name,
        "family": rec.family or "",
        "genus": rec.genus or "",
        "edible": is_edible(rec.extract),
        "description": shorten_description(rec.extract),
        "image_key": normalise_image(rec.image_url),
        "care": None,
        "care_source": "default",
        "provenance": {
            "sources": ["wikipedia", "wikidata", "gbif"],
            "wikipedia_url": rec.wikipedia_url,
            "wikidata_id": rec.wikidata_id,
            "gbif_key": rec.gbif_key,
            "retrieved_at": (now or datetime.now(UTC)).isoformat(timespec="seconds"),
        },
    }


__all__ = ["PlantRecord", "is_edible", "normalise_image", "shorten_description", "to_plant_record"]
