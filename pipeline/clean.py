"""Clean: normalise text and names, enforce taxonomic sanity, dedupe species."""

from __future__ import annotations

import html
import re

from .extract import Rejected
from .records import CleanRecord, RawRecord

MIN_GBIF_CONFIDENCE = 90
_CITATION = re.compile(r"\[(?:\d+|citation needed|note \d+)\]", re.I)
_PARENS_EMPTY = re.compile(r"\(\s*[;,]?\s*\)")
_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
# "Genus species" optionally followed by authorship; keep just the binomial.
_BINOMIAL = re.compile(r"^([A-Z][a-z-]+)\s+(?:\u00d7\s?)?([a-z][a-z-]+)\b")


def clean_text(text: str | None) -> str:
    if not text:
        return ""
    text = html.unescape(_TAG.sub(" ", text))
    text = _CITATION.sub("", text)
    text = _PARENS_EMPTY.sub("", text)
    return _SPACE.sub(" ", text).strip()


def canonical_binomial(name: str | None) -> str | None:
    """'Monstera deliciosa Liebm.' -> 'Monstera deliciosa'; None if not a binomial."""
    if not name:
        return None
    m = _BINOMIAL.match(name.strip())
    return f"{m.group(1)} {m.group(2)}" if m else None


def pick_common_name(raw: RawRecord, scientific_name: str) -> str:
    """Prefer the Wikipedia page title (the name people actually use), then a
    Wikidata English common name, then the binomial itself."""
    title = clean_text(raw.wiki.get("title"))
    if title and title.lower() != scientific_name.lower() and not canonical_binomial(title):
        return _title_case(title)
    for name in raw.wikidata.get("common_names", []):
        name = clean_text(name)
        if name and name.lower() != scientific_name.lower():
            return _title_case(name)
    return scientific_name


def _title_case(name: str) -> str:
    # str.title() mangles "Devil's ivy" -> "Devil'S Ivy"; capitalise words by hand.
    return " ".join(w if w.isupper() else w[:1].upper() + w[1:] for w in name.split())


def clean_record(raw: RawRecord) -> CleanRecord | Rejected:
    gbif = raw.gbif
    if gbif.get("kingdom") != "Plantae":
        return Rejected(raw.candidate, "clean", "not_plantae")
    if (gbif.get("confidence") or 0) < MIN_GBIF_CONFIDENCE:
        return Rejected(raw.candidate, "clean", "low_gbif_confidence")

    # Prefer GBIF's accepted name; fall back to Wikidata's.
    scientific = canonical_binomial(gbif.get("canonicalName")) or canonical_binomial(
        raw.wikidata.get("taxon_name")
    )
    if not scientific:
        return Rejected(raw.candidate, "clean", "no_binomial_name")

    extract = clean_text(raw.wiki.get("extract"))
    if len(extract) < 60:
        return Rejected(raw.candidate, "clean", "description_too_short")

    # Relevance gate: a species nobody has a common name for is almost never
    # something a home gardener is looking for.
    common = pick_common_name(raw, scientific)
    if common == scientific:
        return Rejected(raw.candidate, "clean", "no_common_name")

    return CleanRecord(
        candidate=raw.candidate,
        scientific_name=scientific,
        common_name=common,
        family=gbif.get("family"),
        genus=gbif.get("genus") or scientific.split()[0],
        extract=extract,
        image_url=raw.wiki.get("thumbnail"),
        wikipedia_url=raw.wiki.get("url"),
        gbif_key=gbif.get("usageKey"),
        wikidata_id=raw.wikidata.get("id"),
    )


def dedupe(records: list[CleanRecord]) -> tuple[list[CleanRecord], list[Rejected]]:
    """One record per accepted scientific name; keep the one with the most data.

    Different Wikipedia pages (a species, its cultivar, its synonym) can resolve
    to the same accepted name.
    """
    best: dict[str, CleanRecord] = {}
    dropped: list[Rejected] = []

    def richness(r: CleanRecord) -> tuple[int, int, int]:
        return (int(bool(r.image_url)), int(r.common_name != r.scientific_name), len(r.extract))

    for rec in records:
        key = rec.scientific_name.lower()
        current = best.get(key)
        if current is None:
            best[key] = rec
        elif richness(rec) > richness(current):
            dropped.append(Rejected(current.candidate, "clean", "duplicate_species"))
            best[key] = rec
        else:
            dropped.append(Rejected(rec.candidate, "clean", "duplicate_species"))
    return list(best.values()), dropped


def clean_all(raws: list[RawRecord]) -> tuple[list[CleanRecord], list[Rejected]]:
    kept: list[CleanRecord] = []
    rejected: list[Rejected] = []
    for raw in raws:
        result = clean_record(raw)
        (rejected if isinstance(result, Rejected) else kept).append(result)  # type: ignore[arg-type]
    deduped, dups = dedupe(kept)
    return deduped, rejected + dups
