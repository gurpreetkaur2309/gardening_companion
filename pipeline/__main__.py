"""CLI: ``python -m pipeline run``  (or a single stage).

Stages, in order: discover, extract, clean, transform, enrich, load.
``eval-enrich`` measures Gemini's care answers against the hand-curated seed.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .clean import clean_all
from .enrich import Enricher, compare_to_curated, enrich_rows, load_curated, make_client
from .extract import Rejected, discover_from_list_page, extract
from .http import CachedHttp
from .load import seed_scientific_names, write_catalog
from .records import CleanRecord, RawRecord, read_jsonl, write_jsonl
from .report import build_report, render_markdown
from .transform import to_plant_record
from .validate import validate_all

DEFAULT_LIST_PAGES = [
    "List of culinary herbs and spices",
    "List of garden plants in North America",
    "List of edible flowers",
    "List of companion plants",
    "List of pest-repelling plants",
    "List of vegetables",
    "List of culinary fruits",
    "List of flowers",
    "List of succulent plants",
    "List of poisonous plants",
]

STAGES = ["discover", "extract", "clean", "transform", "enrich", "load", "run", "eval-enrich"]

log = logging.getLogger("pipeline")


def _paths(workdir: Path) -> dict[str, Path]:
    return {
        "candidates": workdir / "candidates.txt",
        "raw": workdir / "raw.jsonl",
        "clean": workdir / "clean.jsonl",
        "transformed": workdir / "transformed.jsonl",
        "enriched": workdir / "enriched.jsonl",
        "enrich_cache": workdir / "enrich_cache",
        "enrich_eval": workdir / "enrich_eval.json",
        "report_json": workdir / "report.json",
        "report_md": workdir / "report.md",
    }


def _save_rejects(workdir: Path, stage: str, rejects: list[Rejected]) -> None:
    """One file per stage, so re-running a stage replaces only its own rejects."""
    write_jsonl(workdir / f"rejects_{stage}.jsonl", [r.__dict__ for r in rejects])


def _load_rejects(workdir: Path) -> list[Rejected]:
    out: list[Rejected] = []
    for f in sorted(workdir.glob("rejects_*.jsonl")):
        out += [Rejected(**r) for r in read_jsonl(f)]
    return out


def _count(path: Path) -> int:
    return sum(1 for _ in read_jsonl(path)) if path.exists() else 0


def cmd_discover(args: argparse.Namespace, http: CachedHttp, p: dict[str, Path]) -> None:
    titles: list[str] = []
    for page in args.list_page or DEFAULT_LIST_PAGES:
        found = discover_from_list_page(http, page)
        log.info("%s: %d links", page, len(found))
        titles.extend(found)
    if args.names_file:
        titles.extend(
            line.strip()
            for line in Path(args.names_file).read_text().splitlines()
            if line.strip() and not line.startswith("#")
        )
    titles = list(dict.fromkeys(titles))
    p["candidates"].parent.mkdir(parents=True, exist_ok=True)
    p["candidates"].write_text("\n".join(titles) + "\n")
    log.info("discover: %d unique candidates", len(titles))


def cmd_extract(args: argparse.Namespace, http: CachedHttp, p: dict[str, Path]) -> None:
    titles = [t for t in p["candidates"].read_text().splitlines() if t]
    raw, rejected = extract(http, titles, limit=args.limit, workers=args.workers)
    write_jsonl(p["raw"], raw)
    _save_rejects(args.workdir, "extract", rejected)
    log.info("extract: %d kept, %d rejected", len(raw), len(rejected))


def cmd_clean(args: argparse.Namespace, p: dict[str, Path]) -> None:
    raws = [RawRecord.model_validate(r) for r in read_jsonl(p["raw"])]
    cleaned, rejected = clean_all(raws)
    write_jsonl(p["clean"], cleaned)
    _save_rejects(args.workdir, "clean", rejected)
    log.info("clean: %d kept, %d rejected", len(cleaned), len(rejected))


def cmd_transform(p: dict[str, Path]) -> None:
    rows = [to_plant_record(CleanRecord.model_validate(r)) for r in read_jsonl(p["clean"])]
    write_jsonl(p["transformed"], rows)
    log.info("transform: %d rows", len(rows))


def cmd_enrich(args: argparse.Namespace, p: dict[str, Path]) -> bool:
    """Returns False (and writes nothing) when no Gemini key is configured."""
    rows = list(read_jsonl(p["transformed"]))
    client = make_client()
    if client is None:
        log.warning("enrich: GEMINI_API_KEY not set — skipping; records keep no care data")
        return False
    enricher = Enricher(client, p["enrich_cache"], rpm=args.rpm, max_calls=args.max_ai_calls)
    out, rejected = enrich_rows(enricher, rows, workers=args.ai_workers)
    write_jsonl(p["enriched"], out)
    _save_rejects(args.workdir, "enrich", rejected)
    with_care = sum(1 for r in out if r.get("care"))
    log.info(
        "enrich: %d in, %d out, %d with care (%d API calls, %d cached)",
        len(rows), len(out), with_care, enricher.calls, enricher.cache_hits,
    )  # fmt: skip
    return True


def cmd_load(args: argparse.Namespace, p: dict[str, Path], http: CachedHttp) -> None:
    source = p["enriched"] if p["enriched"].exists() else p["transformed"]
    valid, invalid = validate_all(list(read_jsonl(source)))
    _save_rejects(args.workdir, "validate", invalid)

    exclude = seed_scientific_names(Path(args.seed_file))
    written = write_catalog(Path(args.out), valid, exclude=exclude)
    final = [v for v in valid if v.scientific_name not in exclude]
    log.info(
        "load: %d valid, %d invalid; wrote %d to %s", len(valid), len(invalid), written, args.out
    )

    report = build_report(
        candidates=len(p["candidates"].read_text().split()) if p["candidates"].exists() else 0,
        extracted=_count(p["raw"]),
        cleaned=_count(p["clean"]),
        rejected=_load_rejects(args.workdir),
        final=final,
        http_stats={"requests": http.requests_made, "cache_hits": http.cache_hits},
    )
    p["report_json"].write_text(json.dumps(report, indent=2))
    p["report_md"].write_text(render_markdown(report))
    log.info("report: %s", p["report_md"])


def cmd_eval_enrich(args: argparse.Namespace, p: dict[str, Path]) -> int:
    client = make_client()
    if client is None:
        print("GEMINI_API_KEY is not set.", file=sys.stderr)
        return 1
    curated = load_curated(Path(args.seed_file))
    enricher = Enricher(client, p["enrich_cache"], rpm=args.rpm, max_calls=args.max_ai_calls)
    result = compare_to_curated(enricher, curated, workers=args.ai_workers)
    p["enrich_eval"].write_text(json.dumps(result, indent=2))
    print(
        f"{result['model']} on {result['species']} curated species "
        f"({result['answered']} answered, ±{result['tolerance']:.0%} tolerance):\n"
        f"  watering   {result['watering_within_tolerance']:.0%}\n"
        f"  fertilizing {result['fertilizing_within_tolerance']:.0%}\n"
        f"  sunlight   {result['sunlight_exact']:.0%}\n"
        f"  edible     {result['edible_exact']:.0%}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pipeline", description=__doc__)
    parser.add_argument("stage", choices=STAGES)
    parser.add_argument("--workdir", default="data/pipeline", type=Path)
    parser.add_argument("--out", default="data/plants_catalog.json")
    parser.add_argument("--seed-file", default="data/plants_seed.json")
    parser.add_argument("--list-page", action="append", help="Wikipedia list page (repeatable)")
    parser.add_argument("--names-file", help="extra Wikipedia titles, one per line")
    parser.add_argument("--limit", type=int, help="stop extracting after N candidates")
    parser.add_argument("--workers", type=int, default=8, help="parallel extract threads")
    parser.add_argument("--ai-workers", type=int, default=4, help="parallel Gemini calls")
    parser.add_argument("--rpm", type=float, default=12, help="Gemini requests per minute")
    parser.add_argument(
        "--max-ai-calls",
        type=int,
        help="API call budget for this run (the cache makes runs resumable)",
    )
    parser.add_argument("--no-enrich", action="store_true", help="skip Gemini enrichment in `run`")
    parser.add_argument("--contact", help="contact for the User-Agent (default: PIPELINE_CONTACT)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = _paths(args.workdir)
    http = CachedHttp(args.workdir / "http_cache", contact=args.contact)
    stage = args.stage

    if stage == "eval-enrich":
        return cmd_eval_enrich(args, p)
    if stage in ("discover", "run"):
        cmd_discover(args, http, p)
    if stage in ("extract", "run"):
        cmd_extract(args, http, p)
    if stage in ("clean", "run"):
        cmd_clean(args, p)
    if stage in ("transform", "run"):
        cmd_transform(p)
        p["enriched"].unlink(missing_ok=True)  # stale once the input changes
    if stage == "enrich" or (stage == "run" and not args.no_enrich):
        cmd_enrich(args, p)
    if stage in ("load", "run"):
        cmd_load(args, p, http)
    return 0


if __name__ == "__main__":
    sys.exit(main())
