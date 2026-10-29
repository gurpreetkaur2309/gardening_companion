"""Enrichment tests with a fake LLM client: bounds, caching, budget, merging."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from pipeline.enrich import (
    BudgetExhaustedError,
    Enricher,
    Enrichment,
    apply_enrichment,
    compare_to_curated,
    enrich_rows,
)

GOOD = {
    "watering_days": 7,
    "fertilizing_days": 30,
    "sunlight": "Partial",
    "fertilizer_type": "Balanced 10-10-10",
    "care_notes": "Overwatering is the usual killer.",
    "sapling_description": "Plant in free-draining mix. Keep the soil lightly moist.",
    "edible": False,
    "relevance": "common",
    "confidence": "high",
}


class FakeClient:
    model = "fake-model"

    def __init__(self, answers=None):
        self.answers = answers or {}
        self.prompts: list[str] = []

    def generate(self, contents, **kwargs):
        self.prompts.append(contents)
        for needle, answer in self.answers.items():
            if needle in contents:
                return None if answer is None else json.dumps({**GOOD, **answer})
        return json.dumps(GOOD)


def enricher(tmp_path, client=None, **kw):
    return Enricher(client or FakeClient(), tmp_path / "cache", rpm=0, backoff=0, **kw)


def row(name="Ocimum basilicum", common="Basil"):
    return {
        "common_name": common,
        "scientific_name": name,
        "edible": False,
        "provenance": {"sources": ["wikipedia"]},
        "care": None,
        "care_source": "default",
    }


# --- schema --------------------------------------------------------------
def test_enrichment_normalises_case():
    e = Enrichment.model_validate(GOOD)
    assert e.sunlight == "partial"


@pytest.mark.parametrize(
    "bad",
    [
        {"watering_days": 0},
        {"watering_days": 500},
        {"fertilizing_days": 1},
        {"sunlight": "moonlight"},
        {"confidence": "certain"},
        {"relevance": "weed"},
    ],
)
def test_enrichment_rejects_out_of_bounds_values(bad):
    with pytest.raises(ValidationError):
        Enrichment.model_validate({**GOOD, **bad})


# --- enricher ------------------------------------------------------------
def test_results_are_cached_by_species(tmp_path):
    client = FakeClient()
    e = enricher(tmp_path, client)
    e.enrich("Basil", "Ocimum basilicum")
    e.enrich("Basil", "Ocimum basilicum")
    assert len(client.prompts) == 1 and e.cache_hits == 1


def test_invalid_model_answer_returns_none_and_is_not_cached(tmp_path):
    client = FakeClient({"Zero": {"watering_days": 0}})
    e = enricher(tmp_path, client)
    assert e.enrich("Zero plant", "Zero zero") is None
    assert not list((tmp_path / "cache").glob("*.json"))


def test_transient_failure_is_retried(tmp_path):
    class Flaky(FakeClient):
        def generate(self, contents, **kwargs):
            self.prompts.append(contents)
            return None if len(self.prompts) < 3 else json.dumps(GOOD)

    client = Flaky()
    assert enricher(tmp_path, client).enrich("Basil", "Ocimum basilicum") is not None
    assert len(client.prompts) == 3


def test_call_budget_stops_further_requests(tmp_path):
    e = enricher(tmp_path, max_calls=1)
    e.enrich("A", "Aaa aaa")
    with pytest.raises(BudgetExhaustedError):
        e.enrich("B", "Bbb bbb")


# --- row merging ---------------------------------------------------------
def test_apply_enrichment_sets_care_provenance_and_source():
    out = apply_enrichment(row(), Enrichment.model_validate(GOOD), model="fake-model")
    assert out["care_source"] == "ai"
    assert out["care"]["watering_days"] == 7 and out["care"]["sunlight"] == "partial"
    assert out["provenance"]["enrichment"]["prompt_version"]
    assert out["provenance"]["sources"] == ["wikipedia"]  # existing provenance kept


def test_enrich_rows_drops_wild_plants_and_withholds_care_when_unsure(tmp_path):
    client = FakeClient(
        {
            "Wild": {"relevance": "not_cultivated"},
            "Guess": {"confidence": "low"},
            "Down": None,
        }
    )
    rows = [row("Good good", "Good"), row("Wild wild", "Wild"), row("Guess guess", "Guess"),
            row("Down down", "Down")]  # fmt: skip
    out, rejected = enrich_rows(enricher(tmp_path, client), rows, workers=1)

    by_name = {r["scientific_name"]: r for r in out}
    assert set(by_name) == {"Good good", "Guess guess", "Down down"}  # wild one dropped
    assert by_name["Good good"]["care"] is not None
    assert by_name["Guess guess"]["care"] is None  # low confidence -> no guessed schedule
    assert by_name["Down down"]["care"] is None
    assert {r.reason for r in rejected} == {
        "not_cultivated",
        "low_confidence_kept_without_care",
        "no_ai_answer_kept_without_care",
    }


def test_enrich_rows_defers_when_budget_is_spent(tmp_path):
    e = enricher(tmp_path, max_calls=1)
    out, rejected = enrich_rows(e, [row("Aaa aaa", "A"), row("Bbb bbb", "B")], workers=1)
    assert len(out) == 2
    assert [r.reason for r in rejected] == ["deferred_call_budget_kept_without_care"]


# --- curated comparison --------------------------------------------------
def test_compare_to_curated_scores_agreement(tmp_path):
    curated = [
        {"common_name": "A", "scientific_name": "Aaa aaa", "edible": False,
         "care": {"watering_days": 7, "fertilizing_days": 30, "sunlight": "partial"}},
        {"common_name": "B", "scientific_name": "Bbb bbb", "edible": True,
         "care": {"watering_days": 2, "fertilizing_days": 30, "sunlight": "full"}},
    ]  # fmt: skip
    result = compare_to_curated(enricher(tmp_path), curated, workers=1)
    assert result["answered"] == 2
    assert result["watering_within_tolerance"] == 0.5  # 7 vs 7 ok; 7 vs 2 not
    assert result["sunlight_exact"] == 0.5
    assert result["edible_exact"] == 0.5
