"""Catalog search: embedding text, indexing, the vector index, and fusion."""

from __future__ import annotations

import pytest

from app.models.enums import CareSource
from app.models.plant import PlantCare
from app.repositories import embedding_repo, plant_repo
from app.services import gemini_service, search_service

# A tiny concept space, so "semantic" behaviour is deterministic: each concept
# is one axis, and several different words light up the same axis.
CONCEPTS = {
    "lowlight": ["shade", "low", "dark", "indirect", "corner"],
    "edible": ["edible", "food", "cook", "kitchen", "herb"],
    "succulent": ["succulent", "drought", "desert", "cactus"],
    "flower": ["flower", "bloom", "blossom", "petal"],
}


class ConceptEmbedder:
    model = "concept-model"
    embed_model = "concept-embed"
    embed_dimensions = len(CONCEPTS)

    def __init__(self):
        self.embed_calls = 0
        self.fail = False

    def generate(self, contents, **kwargs):  # pragma: no cover - unused here
        return None

    def embed(self, texts, *, task_type="RETRIEVAL_DOCUMENT"):
        self.embed_calls += 1
        if self.fail:
            return None
        out = []
        for text in texts:
            low = text.lower()
            vec = [float(sum(low.count(w) for w in words)) for words in CONCEPTS.values()]
            out.append(vec if any(vec) else [0.01] * len(CONCEPTS))
        return out


@pytest.fixture
def embedder(app):
    fake = ConceptEmbedder()
    app.extensions["gemini"] = fake
    search_service._query_cache.clear()
    search_service._index._fingerprint = None  # force a rebuild per test
    return fake


@pytest.fixture
def catalog(app, make_plant, embedder):
    """Four species with distinct concepts and (mostly) disjoint words."""
    with app.app_context():
        fern = make_plant(
            common_name="Boston Fern",
            scientific_name="Nephrolepis exaltata",
            description="Thrives in shade and indirect light.",
        )
        basil = make_plant(
            common_name="Basil",
            scientific_name="Ocimum basilicum",
            description="A kitchen herb for cooking.",
            edible=True,
        )
        aloe = make_plant(
            common_name="Aloe",
            scientific_name="Aloe vera",
            description="A drought tolerant desert succulent.",
        )
        rose = make_plant(
            common_name="Rosemary Rose",
            scientific_name="Rosa rosemaria",
            description="A showy blossom with fragrant petals.",
        )
        search_service.reindex()
    return {"fern": fern, "basil": basil, "aloe": aloe, "rose": rose}


def names(results):
    return [h.plant.common_name for h in results.hits]


# --- embedding text --------------------------------------------------------
def test_embedding_text_includes_what_a_person_would_search_for(make_plant):
    plant = make_plant(
        common_name="Basil", scientific_name="Ocimum basilicum", edible=True, family="Lamiaceae"
    )
    text = search_service.embedding_text(plant)
    assert "Basil (Ocimum basilicum)" in text and "Lamiaceae" in text
    assert "Edible" in text and "Water every 7 days" in text


def test_embedding_text_omits_care_when_it_is_only_the_default(app):
    from bson import ObjectId

    from app.models import Plant

    bare = Plant(
        _id=ObjectId(), common_name="Mystery", care=PlantCare(), care_source=CareSource.DEFAULT
    )
    assert "Water every" not in search_service.embedding_text(bare)


# --- indexing --------------------------------------------------------------
def test_reindex_is_incremental(app, catalog, embedder):
    with app.app_context():
        calls = embedder.embed_calls
        again = search_service.reindex()
        assert (again.embedded, again.skipped) == (0, 4)
        assert embedder.embed_calls == calls  # nothing was sent

        plant_repo.collection.update_one(
            {"_id": catalog["aloe"].id}, {"$set": {"description": "Now a flower."}}
        )
        changed = search_service.reindex()
    assert (changed.embedded, changed.skipped) == (1, 3)


def test_reindex_force_rebuilds_everything(app, catalog):
    with app.app_context():
        assert search_service.reindex(force=True).embedded == 4


def test_reindex_removes_embeddings_of_deleted_plants(app, catalog):
    with app.app_context():
        plant_repo.collection.delete_one({"_id": catalog["rose"].id})
        result = search_service.reindex()
        assert result.removed == 1
        assert catalog["rose"].id not in embedding_repo.hashes()


def test_reindex_reports_failure_without_raising(app, make_plant, embedder):
    make_plant(common_name="Basil")
    embedder.fail = True
    with app.app_context():
        result = search_service.reindex()
    assert result.failed == 1 and result.embedded == 0


def test_reindex_without_a_client_is_a_clean_noop(app, make_plant):
    make_plant(common_name="Basil")
    app.extensions["gemini"] = None
    with app.app_context():
        assert search_service.reindex().failed == 1


# --- the vector index ------------------------------------------------------
def test_numpy_index_ranks_by_cosine_similarity(app, catalog):
    with app.app_context():
        top = search_service.get_index().search([0.0, 1.0, 0.0, 0.0], 2)
    assert top[0][0] == catalog["basil"].id
    assert top[0][1] == pytest.approx(1.0)
    assert top[0][1] >= top[1][1]


def test_numpy_index_sees_new_embeddings_without_a_restart(app, catalog, make_plant):
    with app.app_context():
        index = search_service.get_index()
        assert len(index) == 4
        make_plant(common_name="Cactus", description="A desert cactus.")
        search_service.reindex()
        assert len(index) == 5


def test_numpy_index_on_an_empty_catalog_returns_nothing(app, embedder):
    with app.app_context():
        assert search_service.get_index().search([1.0, 0.0, 0.0, 0.0], 5) == []


# --- search ---------------------------------------------------------------
def test_semantic_search_finds_a_plant_that_shares_no_words_with_the_query(app, catalog):
    with app.app_context():
        results = search_service.search("a dark corner", mode="semantic")
    assert names(results)[0] == "Boston Fern"
    assert results.hits[0].sources == ["semantic"] and results.semantic_used


def test_keyword_ranks_exact_then_prefix_then_substring(app, make_plant, embedder):
    with app.app_context():
        make_plant(common_name="Wild Rose", scientific_name="Rosa canina")
        make_plant(common_name="Rosemary", scientific_name="Salvia rosmarinus")
        make_plant(common_name="Rose", scientific_name="Rosa rubiginosa")
        results = search_service.search("rose", mode="keyword")
    assert names(results) == ["Rose", "Rosemary", "Wild Rose"]
    assert not results.semantic_used


def test_hybrid_rewards_agreement_between_keyword_and_semantic(app, catalog):
    with app.app_context():
        results = search_service.search("rose blossom")
    top = results.hits[0]
    assert top.plant.common_name == "Rosemary Rose"
    assert set(top.sources) == {"keyword", "semantic"}


def test_hybrid_falls_back_to_keywords_when_embeddings_fail(app, catalog, embedder):
    embedder.fail = True
    with app.app_context():
        results = search_service.search("basil")
    assert names(results) == ["Basil"]
    assert results.semantic_used is False


def test_search_without_any_client_still_returns_keyword_hits(app, catalog):
    app.extensions["gemini"] = None
    with app.app_context():
        results = search_service.search("aloe")
    assert names(results) == ["Aloe"] and not results.semantic_used


def test_query_embeddings_are_cached(app, catalog, embedder):
    with app.app_context():
        search_service.search("dark corner")
        calls = embedder.embed_calls
        search_service.search("Dark Corner")
    assert embedder.embed_calls == calls


def test_blank_query_returns_nothing(app, catalog):
    with app.app_context():
        assert search_service.search("   ").hits == []


def test_gemini_service_exposes_the_same_embed_signature(app):
    # The real client and the fakes must stay interchangeable.
    import inspect

    real = inspect.signature(gemini_service.GeminiClient.embed)
    assert "task_type" in real.parameters


# --- HTTP ------------------------------------------------------------------
def test_api_search_requires_login(client):
    assert client.get("/api/v1/catalog/search?q=fern").status_code in (301, 302, 401)


def test_api_search_returns_scored_results(app, client, login, user, catalog):
    login(user)
    response = client.get("/api/v1/catalog/search?q=dark%20corner&mode=semantic")
    body = response.get_json()
    assert response.status_code == 200
    assert body["semantic_used"] is True
    assert body["results"][0]["common_name"] == "Boston Fern"
    assert body["results"][0]["matched_by"] == ["semantic"]
    assert 0 < body["results"][0]["similarity"] <= 1


@pytest.mark.parametrize("qs", ["", "?q=", "?q=fern&mode=bogus", "?q=" + "x" * 201])
def test_api_search_validates_input(client, login, user, catalog, qs):
    login(user)
    url = "/api/v1/catalog/search" + (qs if qs.startswith("?") else "")
    assert client.get(url).status_code == 400


def test_catalog_page_renders_search_results(client, login, user, catalog):
    login(user)
    response = client.get("/gardens/catalog?q=dark+corner")
    assert response.status_code == 200
    assert b"Boston Fern" in response.data
    assert b"best matches" in response.data


def test_multi_word_keyword_queries_match_on_any_word_and_rank_by_coverage(
    app, make_plant, embedder
):
    with app.app_context():
        make_plant(common_name="Rose", scientific_name="Rosa rubiginosa")
        make_plant(common_name="Rose Mallow", scientific_name="Hibiscus moscheutos")
        make_plant(common_name="Mallow", scientific_name="Malva sylvestris")
        results = search_service.search("rose mallow", mode="keyword")
    assert names(results) == ["Rose Mallow", "Mallow", "Rose"]
