"""Data-quality report: what went in, what came out, and why things were dropped."""

from __future__ import annotations

from collections import Counter
from typing import Any

from .extract import Rejected
from .records import PlantRecord


def build_report(
    *,
    candidates: int,
    extracted: int,
    cleaned: int,
    rejected: list[Rejected],
    final: list[PlantRecord],
    http_stats: dict[str, int] | None = None,
) -> dict[str, Any]:
    n = len(final) or 1
    enriched = [p for p in final if p.provenance.get("enrichment")]
    return {
        "funnel": {
            "candidates": candidates,
            "extracted": extracted,
            "cleaned": cleaned,
            "final": len(final),
        },
        "rejected_by_reason": dict(
            Counter(f"{r.stage}:{r.reason}" for r in rejected).most_common()
        ),
        "completeness": {
            "with_image": round(sum(1 for p in final if p.image_key) / n, 3),
            "with_distinct_common_name": round(
                sum(1 for p in final if p.common_name != p.scientific_name) / n, 3
            ),
            "edible": round(sum(1 for p in final if p.edible) / n, 3),
            "with_care": round(sum(1 for p in final if p.care) / n, 3),
        },
        "enrichment": {
            "enriched": len(enriched),
            "confidence": dict(Counter(p.provenance["enrichment"]["confidence"] for p in enriched)),
            "relevance": dict(Counter(p.provenance["enrichment"]["relevance"] for p in enriched)),
        },
        "top_families": dict(Counter(p.family for p in final).most_common(10)),
        "http": http_stats or {},
    }


def render_markdown(report: dict[str, Any]) -> str:
    f = report["funnel"]
    lines = [
        "# Catalog data-quality report",
        "",
        "| Stage | Records |",
        "|---|---|",
        *(f"| {k} | {v} |" for k, v in f.items()),
        "",
        "## Rejections",
        "",
        "| Stage:reason | Count |",
        "|---|---|",
        *(f"| {k} | {v} |" for k, v in report["rejected_by_reason"].items()),
        "",
        "## Completeness of the final set",
        "",
        *(f"- {k.replace('_', ' ')}: {v:.0%}" for k, v in report["completeness"].items()),
        "",
        "## AI enrichment",
        "",
        f"- records enriched: {report['enrichment']['enriched']}",
        f"- confidence: {report['enrichment']['confidence']}",
        f"- relevance: {report['enrichment']['relevance']}",
        "",
        "## Top families",
        "",
        *(f"- {k}: {v}" for k, v in report["top_families"].items()),
        "",
    ]
    return "\n".join(lines)
