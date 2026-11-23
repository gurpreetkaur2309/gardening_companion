"""Pipeline tests: no network. HTTP is faked at the CachedHttp boundary."""

from __future__ import annotations

import json

import pytest

from pipeline.clean import (
    canonical_binomial,
    clean_all,
    clean_record,
    clean_text,
    pick_common_name,
)
from pipeline.extract import (
    GBIF_MATCH,
    GBIF_SPECIES,
    WIKI_SUMMARY,
    WIKIDATA_ENTITY,
    Rejected,
    discover_from_list_page,
    extract_one,
)
from pipeline.http import CachedHttp
from pipeline.load import seed_scientific_names, write_catalog
from pipeline.records import PlantRecord, RawRecord
from pipeline.report import build_report
from pipeline.transform import is_edible, normalise_image, shorten_description, to_plant_record
from pipeline.validate import validate_all

LONG = (
    "Ocimum basilicum, commonly known as basil, is a culinary herb of the family "
    "Lamiaceae. It is a tender plant, and is used in cuisines worldwide."
)


def raw(**over) -> RawRecord:
    base = {
        "candidate": "Basil",
        "wiki": {
            "title": "Basil",
            "extract": LONG,
            "thumbnail": "https://thumb.wikimedia.org/wikipedia/commons/thumb/a/ab/B.jpg/330px-B.jpg?x=1",
            "url": "https://en.wikipedia.org/wiki/Basil",
        },
        "wikidata": {
            "id": "Q38",
            "taxon_name": "Ocimum basilicum",
            "common_names": ["Sweet basil"],
        },
        "gbif": {
            "usageKey": 1,
            "canonicalName": "Ocimum basilicum",
            "kingdom": "Plantae",
            "confidence": 99,
            "family": "Lamiaceae",
            "genus": "Ocimum",
        },
    }
    for k, v in over.items():
        base[k] = {**base[k], **v} if isinstance(v, dict) else v
    return RawRecord.model_validate(base)


# --- clean ---------------------------------------------------------------
def test_clean_text_strips_html_citations_and_whitespace():
    assert (
        clean_text("A <b>plant</b>[1] \n grows&nbsp;fast ( ) [citation needed]")
        == "A plant grows fast"
    )


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Monstera deliciosa Liebm.", "Monstera deliciosa"),
        ("Ocimum basilicum", "Ocimum basilicum"),
        ("Mentha \u00d7 piperita L.", "Mentha piperita"),
        ("Monstera", None),
        (None, None),
    ],
)
def test_canonical_binomial(name, expected):
    assert canonical_binomial(name) == expected


def test_common_name_prefers_page_title_over_wikidata():
    assert pick_common_name(raw(), "Ocimum basilicum") == "Basil"


def test_common_name_falls_back_to_wikidata_when_title_is_a_binomial():
    r = raw(wiki={"title": "Ocimum basilicum"})
    assert pick_common_name(r, "Ocimum basilicum") == "Sweet Basil"


def test_common_name_title_casing_keeps_apostrophes():
    r = raw(wiki={"title": "Devil's ivy"})
    assert pick_common_name(r, "Epipremnum aureum") == "Devil's Ivy"


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"gbif": {"kingdom": "Animalia"}}, "not_plantae"),
        ({"gbif": {"confidence": 70}}, "low_gbif_confidence"),
        (
            {
                "gbif": {"canonicalName": "Ocimum", "kingdom": "Plantae"},
                "wikidata": {"taxon_name": "Ocimum"},
            },
            "no_binomial_name",
        ),
        ({"wiki": {"extract": "Too short."}}, "description_too_short"),
        (
            {"wiki": {"title": "Ocimum basilicum"}, "wikidata": {"common_names": []}},
            "no_common_name",
        ),
    ],
)
def test_clean_rejects_with_a_reason(override, reason):
    result = clean_record(raw(**override))
    assert isinstance(result, Rejected) and result.reason == reason


def test_dedupe_keeps_the_richest_record_for_a_species():
    plain = raw(candidate="Sweet basil", wiki={"thumbnail": None})
    rich = raw(candidate="Basil")
    kept, rejected = clean_all([plain, rich])
    assert [k.candidate for k in kept] == ["Basil"]
    assert [r.reason for r in rejected] == ["duplicate_species"]


# --- transform -----------------------------------------------------------
def test_shorten_description_stops_on_a_sentence_boundary():
    text = "First sentence is here. " + "Second one is also fine. " * 30
    out = shorten_description(text, limit=80)
    assert out.endswith(".") and len(out) <= 80 and out.startswith("First sentence")


def test_shorten_description_truncates_one_enormous_sentence():
    out = shorten_description("word " * 200, limit=50)
    assert len(out) <= 50 and out.endswith("…")


def test_edible_heuristic_lets_toxicity_win():
    assert is_edible("A culinary herb.")
    assert not is_edible("A culinary herb, but all parts are poisonous.")
    assert not is_edible("An ornamental flower.")


def test_normalise_image_uses_canonical_host_and_standard_width():
    url = "https://thumb.wikimedia.org/wikipedia/commons/thumb/a/ab/B.jpg/330px-B.jpg?utm=1"
    assert normalise_image(url) == (
        "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/B.jpg/500px-B.jpg"
    )
    assert normalise_image(None) is None


def test_transform_output_matches_the_catalog_shape():
    cleaned = clean_record(raw())
    row = to_plant_record(cleaned)
    assert row["common_name"] == "Basil" and row["edible"] is True
    assert row["care"] is None and row["care_source"] == "default"
    assert row["provenance"]["gbif_key"] == 1
    PlantRecord.model_validate(row)


# --- validate / load / report -------------------------------------------
def test_validate_splits_good_and_bad_rows():
    good = to_plant_record(clean_record(raw()))
    bad = {**good, "scientific_name": "notabinomial"}
    valid, rejected = validate_all([good, bad])
    assert len(valid) == 1
    assert rejected[0].reason == "invalid_scientific_name"


def test_write_catalog_skips_curated_species_and_sorts(tmp_path):
    a = PlantRecord.model_validate(to_plant_record(clean_record(raw())))
    b = a.model_copy(update={"scientific_name": "Aloe vera", "common_name": "Aloe"})
    out = tmp_path / "catalog.json"
    assert write_catalog(out, [a, b], exclude={"ocimum basilicum"}) == 1
    assert [r["scientific_name"] for r in json.loads(out.read_text())] == ["Aloe vera"]


def test_seed_scientific_names_reads_the_real_seed_file():
    names = seed_scientific_names(__import__("pathlib").Path("data/plants_seed.json"))
    assert "Monstera deliciosa" in names and len(names) == 40


def test_report_counts_and_completeness():
    a = PlantRecord.model_validate(to_plant_record(clean_record(raw())))
    report = build_report(
        candidates=10,
        extracted=4,
        cleaned=2,
        rejected=[Rejected("x", "clean", "not_plantae"), Rejected("y", "clean", "not_plantae")],
        final=[a],
    )
    assert report["rejected_by_reason"] == {"clean:not_plantae": 2}
    assert report["completeness"]["with_image"] == 1.0
    assert report["funnel"]["final"] == 1


# --- extract (HTTP faked) ------------------------------------------------
class FakeHttp:
    def __init__(self, routes):
        self.routes = routes

    def get_json(self, url, params=None):
        return self.routes.get(
            url if not params else (url, params.get("name") or params.get("page"))
        )


def _routes(**over):
    routes = {
        WIKI_SUMMARY + "Basil": {
            "type": "standard",
            "title": "Basil",
            "extract": LONG,
            "wikibase_item": "Q38",
            "thumbnail": {"source": "https://x/y.jpg"},
            "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Basil"}},
        },
        WIKIDATA_ENTITY.format("Q38"): {
            "entities": {
                "Q38": {
                    "claims": {
                        "P225": [{"mainsnak": {"datavalue": {"value": "Ocimum basilicum"}}}],
                        "P105": [{"mainsnak": {"datavalue": {"value": {"id": "Q7432"}}}}],
                        "P1843": [
                            {
                                "mainsnak": {
                                    "datavalue": {
                                        "value": {"language": "en", "text": "Sweet basil"}
                                    }
                                }
                            },
                            {
                                "mainsnak": {
                                    "datavalue": {"value": {"language": "de", "text": "Basilikum"}}
                                }
                            },
                        ],
                    }
                }
            }
        },
        (GBIF_MATCH, "Ocimum basilicum"): {
            "usageKey": 1,
            "canonicalName": "Ocimum basilicum",
            "kingdom": "Plantae",
            "matchType": "EXACT",
            "confidence": 99,
            "status": "ACCEPTED",
            "family": "Lamiaceae",
            "genus": "Ocimum",
        },
    }
    routes.update(over)
    return routes


def test_extract_one_resolves_title_to_taxon_and_filters_languages():
    result = extract_one(FakeHttp(_routes()), "Basil")
    assert isinstance(result, RawRecord)
    assert result.wikidata["taxon_name"] == "Ocimum basilicum"
    assert result.wikidata["common_names"] == ["Sweet basil"]
    assert result.gbif["family"] == "Lamiaceae"


def test_extract_one_rejects_non_taxa_and_non_species():
    assert extract_one(FakeHttp({}), "France").reason == "no_wikipedia_page"
    no_claims = _routes(**{WIKIDATA_ENTITY.format("Q38"): {"entities": {"Q38": {"claims": {}}}}})
    assert extract_one(FakeHttp(no_claims), "Basil").reason == "not_a_taxon"
    genus = _routes()
    genus[WIKIDATA_ENTITY.format("Q38")]["entities"]["Q38"]["claims"]["P105"] = [
        {"mainsnak": {"datavalue": {"value": {"id": "Q34740"}}}}
    ]
    assert extract_one(FakeHttp(genus), "Basil").reason == "not_a_species"


def test_extract_one_follows_a_gbif_synonym_to_the_accepted_name():
    routes = _routes()
    routes[(GBIF_MATCH, "Ocimum basilicum")] = {
        "usageKey": 9,
        "canonicalName": "Old name",
        "status": "SYNONYM",
        "acceptedUsageKey": 1,
        "matchType": "EXACT",
        "confidence": 98,
        "kingdom": "Plantae",
    }
    routes[GBIF_SPECIES.format(1)] = {
        "key": 1,
        "canonicalName": "Ocimum basilicum",
        "family": "Lamiaceae",
        "genus": "Ocimum",
        "rank": "SPECIES",
    }
    result = extract_one(FakeHttp(routes), "Basil")
    assert result.gbif["canonicalName"] == "Ocimum basilicum"
    assert result.gbif["resolved_synonym"] is True


def test_discover_keeps_only_existing_article_links():
    page = {
        "parse": {
            "links": [
                {"ns": 0, "exists": "", "*": "Basil"},
                {"ns": 0, "*": "Red link"},
                {"ns": 14, "exists": "", "*": "Category:Herbs"},
                {"ns": 0, "exists": "", "*": "List of other things"},
                {"ns": 0, "exists": "", "*": "Basil"},
            ]
        }
    }
    http = FakeHttp({("https://en.wikipedia.org/w/api.php", "L"): page})
    assert discover_from_list_page(http, "L") == ["Basil"]


# --- http cache ----------------------------------------------------------
class StubResponse:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self.ok = status < 400
        self._body = body

    def json(self):
        return self._body


class StubSession:
    def __init__(self, responses):
        self.headers = {}
        self.responses = list(responses)
        self.calls = 0

    def mount(self, *_):
        pass

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        return self.responses.pop(0)


def test_cached_http_hits_the_network_once_and_caches_404s(tmp_path):
    session = StubSession([StubResponse(200, {"a": 1}), StubResponse(404)])
    http = CachedHttp(tmp_path, session=session, min_interval=0)
    assert http.get_json("https://x/1") == {"a": 1}
    assert http.get_json("https://x/1") == {"a": 1}
    assert http.get_json("https://x/2") is None
    assert http.get_json("https://x/2") is None
    assert session.calls == 2 and http.cache_hits == 2


def test_cached_http_does_not_cache_server_errors(tmp_path):
    session = StubSession([StubResponse(503), StubResponse(200, {"ok": True})])
    http = CachedHttp(tmp_path, session=session, min_interval=0)
    assert http.get_json("https://x/1") is None
    assert http.get_json("https://x/1") == {"ok": True}


def test_user_agent_carries_the_project_contact(tmp_path):
    http = CachedHttp(tmp_path, session=StubSession([]))
    assert "gurpreetkhanuja2309@gmail.com" in http.session.headers["User-Agent"]
