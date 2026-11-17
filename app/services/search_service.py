"""Catalog search: keyword, semantic, and the two fused.

Semantic search embeds every species once (``reindex``) and answers a query by
cosine similarity against the whole catalog.  At catalog scale (thousands of
species) an exact in-process scan is a millisecond-class operation and needs no
vector database; the ``VectorIndex`` protocol is the seam where Atlas Vector
Search or pgvector would go if that ever stops being true.

Hybrid search fuses the keyword and semantic rankings with reciprocal rank
fusion.  Keywords catch what embeddings blur ("monstera" the exact plant),
embeddings catch what keywords cannot ("pet-safe, low light").  If the
embedding service is unavailable, search silently degrades to keywords only.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np
from bson import ObjectId

from ..models import Plant
from ..repositories import embedding_repo, plant_repo
from . import gemini_service

logger = logging.getLogger(__name__)

RRF_K = 60  # standard reciprocal-rank-fusion constant
CANDIDATES = 50  # how deep each ranking contributes before fusion
EMBED_BATCH = 50
QUERY_CACHE_SIZE = 256


# ---------------------------------------------------------------------------
# Text that gets embedded
# ---------------------------------------------------------------------------
_SUN_WORDS = {
    "full": "full sun, direct sunlight",
    "partial": "partial sun, bright light",
    "shade": "shade, low light, indirect light",
}


def embedding_text(plant: Plant) -> str:
    """What the model reads about a species. Changing this re-embeds everything."""
    parts = [f"{plant.common_name} ({plant.scientific_name})" if plant.scientific_name
             else plant.common_name]  # fmt: skip
    if plant.family:
        parts.append(f"Family: {plant.family}.")
    if plant.description:
        parts.append(plant.description)
    if plant.has_care_data:
        parts.append(f"Light: {_SUN_WORDS.get(str(plant.care.sunlight), plant.care.sunlight)}.")
        parts.append(f"Water every {plant.care.watering_days} days.")
        if plant.care.notes:
            parts.append(plant.care.notes)
    if plant.edible:
        parts.append("Edible plant, grown for food.")
    return " ".join(parts)


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Vector index
# ---------------------------------------------------------------------------
class VectorIndex(Protocol):
    def search(self, query: list[float], k: int) -> list[tuple[ObjectId, float]]:
        """Top-``k`` ``(plant_id, cosine_similarity)``, best first."""
        ...


class NumpyIndex:
    """Exact cosine search over an in-memory matrix, rebuilt when the data changes."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._fingerprint: object = None
        self._ids: list[ObjectId] = []
        self._matrix = np.zeros((0, 0), dtype=np.float32)

    def _refresh(self) -> None:
        fingerprint = embedding_repo.fingerprint()
        with self._lock:
            if fingerprint == self._fingerprint:
                return
            docs = embedding_repo.all_vectors()
            if docs:
                # Mixed dimensions mean a model change mid-reindex; use the majority.
                counts: dict[int, int] = {}
                for d in docs:
                    counts[d["dimensions"]] = counts.get(d["dimensions"], 0) + 1
                dims = max(counts, key=lambda n: counts[n])
                docs = [d for d in docs if d["dimensions"] == dims]
                matrix = np.asarray([d["vector"] for d in docs], dtype=np.float32)
                norms = np.linalg.norm(matrix, axis=1, keepdims=True)
                matrix = (matrix / np.where(norms == 0, 1, norms)).astype(np.float32)
            else:
                matrix = np.zeros((0, 0), dtype=np.float32)
            self._ids = [d["_id"] for d in docs]
            self._matrix = matrix
            self._fingerprint = fingerprint

    def search(self, query: list[float], k: int) -> list[tuple[ObjectId, float]]:
        self._refresh()
        matrix = self._matrix
        q = np.asarray(query, dtype=np.float32)
        if matrix.shape[0] == 0 or matrix.shape[1] != q.shape[0]:
            return []
        norm = np.linalg.norm(q)
        scores = matrix @ (q / (norm or 1.0))
        k = min(k, scores.shape[0])
        top = np.argpartition(-scores, k - 1)[:k]
        top = top[np.argsort(-scores[top])]
        return [(self._ids[i], float(scores[i])) for i in top]

    def __len__(self) -> int:
        self._refresh()
        return len(self._ids)


_index = NumpyIndex()


def get_index() -> VectorIndex:
    return _index


# ---------------------------------------------------------------------------
# Indexing
# ---------------------------------------------------------------------------
@dataclass
class ReindexResult:
    total: int = 0
    embedded: int = 0
    skipped: int = 0
    failed: int = 0
    removed: int = 0


def _is_current(stored: str | None, model: str, dims: int | None, digest: str) -> bool:
    """``stored`` is the repo's ``model|dimensions|hash`` tag for a species."""
    if not stored:
        return False
    stored_model, stored_dims, stored_hash = stored.split("|")
    return (
        stored_model == model
        and stored_hash == digest
        and (dims is None or stored_dims == str(dims))
    )


def reindex(*, force: bool = False, batch_size: int = EMBED_BATCH) -> ReindexResult:
    """Embed species whose text changed (or all, with ``force``). Idempotent."""
    client = gemini_service.get_client()
    result = ReindexResult()
    plants = plant_repo.list_all(limit=100_000)
    result.total = len(plants)
    result.removed = embedding_repo.delete_orphans({p.id for p in plants})
    if client is None:
        logger.warning("reindex: no embedding client configured")
        result.failed = result.total
        return result

    model_tag = f"{getattr(client, 'embed_model', client.model)}"
    dims = getattr(client, "embed_dimensions", None)
    existing = {} if force else embedding_repo.hashes()

    pending: list[tuple[Plant, str, str]] = []
    for plant in plants:
        text = embedding_text(plant)
        digest = content_hash(text)
        if _is_current(existing.get(plant.id), model_tag, dims, digest):
            result.skipped += 1
        else:
            pending.append((plant, text, digest))

    per_minute = getattr(client, "embed_per_minute", None) or 0
    for start in range(0, len(pending), batch_size):
        batch = pending[start : start + batch_size]
        texts = [t for _, t, _ in batch]
        vectors = client.embed(texts, task_type="RETRIEVAL_DOCUMENT")
        if vectors is None and per_minute:
            # Most likely a per-minute quota: wait it out once, then retry.
            time.sleep(60)
            vectors = client.embed(texts, task_type="RETRIEVAL_DOCUMENT")
        if vectors is None:
            result.failed += len(batch)
            continue
        embedding_repo.upsert_many(
            [(p.id, digest, vec) for (p, _, digest), vec in zip(batch, vectors, strict=True)],
            model=model_tag,
        )
        result.embedded += len(batch)
        if per_minute and start + batch_size < len(pending):
            time.sleep(len(batch) * 60 / per_minute)  # stay under the quota
        logger.info("reindex: %d/%d embedded", result.embedded, len(pending))
    return result


# ---------------------------------------------------------------------------
# Querying
# ---------------------------------------------------------------------------
@dataclass
class Hit:
    plant: Plant
    score: float
    sources: list[str] = field(default_factory=list)  # "keyword" and/or "semantic"
    similarity: float | None = None  # cosine, when semantic contributed


@dataclass
class SearchResults:
    hits: list[Hit]
    semantic_used: bool
    query: str


_query_cache: OrderedDict[tuple[str, str], list[float]] = OrderedDict()
_query_cache_lock = threading.Lock()


def _embed_query(query: str) -> list[float] | None:
    client = gemini_service.get_client()
    if client is None:
        return None
    key = (client.model, query.lower())
    with _query_cache_lock:
        if key in _query_cache:
            _query_cache.move_to_end(key)
            return _query_cache[key]
    vectors = client.embed([query], task_type="RETRIEVAL_QUERY")
    if not vectors:
        return None
    with _query_cache_lock:
        _query_cache[key] = vectors[0]
        while len(_query_cache) > QUERY_CACHE_SIZE:
            _query_cache.popitem(last=False)
    return vectors[0]


def _keyword_tier(plant: Plant, q: str) -> int:
    """Lower is better: exact name, then prefix, then word start, then substring."""
    names = [n.lower() for n in (plant.common_name, plant.scientific_name or "") if n]
    if q in names:
        return 0
    if any(n.startswith(q) for n in names):
        return 1
    if any(f" {q}" in n for n in names):
        return 2
    return 3


def _coverage(plant: Plant, tokens: list[str]) -> int:
    """How many distinct query words appear in the plant's names."""
    haystack = " ".join(
        s.lower() for s in (plant.common_name, plant.scientific_name, plant.genus) if s
    )
    return sum(1 for t in tokens if t in haystack)


def keyword_search(query: str, limit: int = CANDIDATES) -> list[Plant]:
    """Any query word may match; more words matched ranks higher, then name quality."""
    q = query.strip().lower()
    tokens = list(dict.fromkeys(t for t in re.findall(r"\w+", q) if len(t) >= 2))
    found = plant_repo.find_by_terms(tokens)
    found.sort(key=lambda p: (-_coverage(p, tokens), _keyword_tier(p, q), p.common_name.lower()))
    return found[:limit]


def semantic_search(query: str, limit: int = CANDIDATES) -> list[tuple[Plant, float]] | None:
    """``None`` means semantic search is unavailable (not "no results")."""
    vector = _embed_query(query)
    if vector is None:
        return None
    ranked = get_index().search(vector, limit)
    by_id = plant_repo.get_many([pid for pid, _ in ranked])
    return [(by_id[pid], sim) for pid, sim in ranked if pid in by_id]


def search(query: str, *, limit: int = 20, mode: str = "hybrid") -> SearchResults:
    """``mode`` is ``hybrid`` (default), ``keyword`` or ``semantic``."""
    query = query.strip()
    if not query:
        return SearchResults([], False, query)

    keyword = keyword_search(query) if mode in ("hybrid", "keyword") else []
    semantic = semantic_search(query) if mode in ("hybrid", "semantic") else []

    hits: dict[ObjectId, Hit] = {}
    for rank, plant in enumerate(keyword):
        hit = hits.setdefault(plant.id, Hit(plant, 0.0))
        hit.score += 1.0 / (RRF_K + rank + 1)
        hit.sources.append("keyword")
    for rank, (plant, sim) in enumerate(semantic or []):
        hit = hits.setdefault(plant.id, Hit(plant, 0.0))
        hit.score += 1.0 / (RRF_K + rank + 1)
        hit.sources.append("semantic")
        hit.similarity = sim

    ordered = sorted(hits.values(), key=lambda h: (-h.score, h.plant.common_name.lower()))
    return SearchResults(ordered[:limit], semantic is not None and mode != "keyword", query)
