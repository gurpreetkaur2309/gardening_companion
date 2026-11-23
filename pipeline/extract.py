"""Extract: turn Wikipedia list pages into raw taxon records.

Resolution chain for one candidate page title:

    Wikipedia summary  ->  Wikidata taxon (name, rank, English common name)
                       ->  GBIF backbone match (authoritative family / genus)

Wikidata is what lets us accept "Basil" (a common name) and still end up with
*Ocimum basilicum*; it also cheaply rejects the many list links that are not
plants at all (countries, people, concepts) because they have no taxon name.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from urllib.parse import quote

from .http import CachedHttp
from .records import RawRecord

logger = logging.getLogger(__name__)

WIKI_API = "https://en.wikipedia.org/w/api.php"
WIKI_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/"
WIKIDATA_ENTITY = "https://www.wikidata.org/wiki/Special:EntityData/{}.json"
GBIF_MATCH = "https://api.gbif.org/v1/species/match"
GBIF_SPECIES = "https://api.gbif.org/v1/species/{}"

WIKIDATA_RANK_SPECIES = "Q7432"
# Titles that are never a plant, whatever they link to. Cheap, saves API calls.
_SKIP_TITLE = re.compile(
    r"^(List of|Category:|Template:|Help:|Wikipedia:|Portal:|File:|Talk:)|\(disambiguation\)",
    re.I,
)


@dataclass
class Rejected:
    key: str
    stage: str
    reason: str


def discover_from_list_page(http: CachedHttp, page: str) -> list[str]:
    """Article titles linked from a Wikipedia list page."""
    data = http.get_json(
        WIKI_API,
        {"action": "parse", "page": page, "prop": "links", "format": "json", "redirects": 1},
    )
    if not data or "parse" not in data:
        logger.warning("List page %r not found", page)
        return []
    titles = [
        link["*"]
        for link in data["parse"].get("links", [])
        if link.get("ns") == 0 and "exists" in link and not _SKIP_TITLE.search(link["*"])
    ]
    return list(dict.fromkeys(titles))


def _claim_value(claims: dict, prop: str):  # type: ignore[type-arg]
    for claim in claims.get(prop, []):
        value = claim.get("mainsnak", {}).get("datavalue", {}).get("value")
        if value is not None:
            return value
    return None


def _english_common_names(claims: dict) -> list[str]:  # type: ignore[type-arg]
    names = []
    for claim in claims.get("P1843", []):
        value = claim.get("mainsnak", {}).get("datavalue", {}).get("value") or {}
        if value.get("language") == "en" and value.get("text"):
            names.append(value["text"])
    return names


def extract_one(http: CachedHttp, title: str) -> RawRecord | Rejected:
    summary = http.get_json(WIKI_SUMMARY + quote(title.replace(" ", "_"), safe=""))
    if not summary or summary.get("type") != "standard":
        return Rejected(title, "extract", "no_wikipedia_page")
    qid = summary.get("wikibase_item")
    if not qid:
        return Rejected(title, "extract", "no_wikidata_item")

    entity = http.get_json(WIKIDATA_ENTITY.format(qid))
    claims = ((entity or {}).get("entities", {}).get(qid, {})).get("claims", {})
    taxon_name = _claim_value(claims, "P225")
    if not isinstance(taxon_name, str):
        return Rejected(title, "extract", "not_a_taxon")
    rank = _claim_value(claims, "P105")
    rank_id = rank.get("id") if isinstance(rank, dict) else None
    if rank_id != WIKIDATA_RANK_SPECIES:
        return Rejected(title, "extract", "not_a_species")

    match = http.get_json(GBIF_MATCH, {"name": taxon_name, "kingdom": "Plantae"})
    if not match or match.get("matchType") == "NONE":
        return Rejected(title, "extract", "no_gbif_match")

    # A synonym resolves to its accepted name; take taxonomy from that record.
    if match.get("status") == "SYNONYM" and match.get("acceptedUsageKey"):
        accepted = http.get_json(GBIF_SPECIES.format(match["acceptedUsageKey"]))
        if accepted:
            match = {
                **match,
                **{k: accepted.get(k) for k in ("canonicalName", "family", "genus", "rank")},
            }
            match["usageKey"] = accepted.get("key", match.get("usageKey"))
            match["resolved_synonym"] = True

    return RawRecord(
        candidate=title,
        wiki={
            "title": summary.get("title"),
            "extract": summary.get("extract"),
            "thumbnail": (summary.get("thumbnail") or {}).get("source"),
            "url": ((summary.get("content_urls") or {}).get("desktop") or {}).get("page"),
        },
        wikidata={
            "id": qid,
            "taxon_name": taxon_name,
            "rank_id": rank_id,
            "common_names": _english_common_names(claims),
        },
        gbif={
            k: match.get(k)
            for k in (
                "usageKey",
                "canonicalName",
                "rank",
                "status",
                "matchType",
                "confidence",
                "kingdom",
                "family",
                "genus",
                "resolved_synonym",
            )
        },
    )


def extract(
    http: CachedHttp, titles: Iterable[str], *, limit: int | None = None, workers: int = 8
) -> tuple[list[RawRecord], list[Rejected]]:
    batch = list(titles)[:limit]
    raw: list[RawRecord] = []
    rejected: list[Rejected] = []
    # I/O bound: threads overlap network latency; the shared client still
    # enforces the global request rate. map() keeps results in input order.
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, result in enumerate(pool.map(lambda t: extract_one(http, t), batch)):
            (rejected if isinstance(result, Rejected) else raw).append(result)  # type: ignore[arg-type]
            if (i + 1) % 200 == 0:
                logger.info("extracted %d/%d (kept %d)", i + 1, len(batch), len(raw))
    return raw, rejected
