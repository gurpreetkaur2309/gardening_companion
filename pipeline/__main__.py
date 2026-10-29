"""CLI: ``python -m pipeline run``  (or a single stage)."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .clean import clean_all
from .extract import Rejected, discover_from_list_page, extract
from .http import CachedHttp
from .load import seed_scientific_names, write_catalog
from .records import CleanRecord, PlantRecord, RawRecord, read_jsonl, write_jsonl
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

log = logging.getLogger("pipeline")


def _paths(workdir: Path) -> dict[str, Path]:
    return {
        "candidates": workdir / "candidates.txt",
        "raw": workdir / "raw.jsonl",
        "clean": workdir / "clean.jsonl",
        "transformed": workdir / "transformed.jsonl",
        "rejects": workdir / "rejects.jsonl",
        "report_json": workdir / "report.json",
        "report_md": workdir / "report.md",
    }


def _write_rejects(path: Path, rejects: list[Rejected]) -> None:
    write_jsonl(path, [r.__dict__ for r in rejects])


def cmd_discover(args: argparse.Namespace, http: CachedHttp, p: dict[str, Path]) -> list[str]:
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
    return titles


def cmd_extract(args: argparse.Namespace, http: CachedHttp, p: dict[str, Path]) -> list[Rejected]:
    titles = [t for t in p["candidates"].read_text().splitlines() if t]
    raw, rejected = extract(http, titles, limit=args.limit, workers=args.workers)
    write_jsonl(p["raw"], raw)
    log.info("extract: %d kept, %d rejected", len(raw), len(rejected))
    return rejected


def cmd_clean(p: dict[str, Path]) -> list[Rejected]:
    raws = [RawRecord.model_validate(r) for r in read_jsonl(p["raw"])]
    cleaned, rejected = clean_all(raws)
    write_jsonl(p["clean"], cleaned)
    log.info("clean: %d kept, %d rejected", len(cleaned), len(rejected))
    return rejected


def cmd_transform(p: dict[str, Path]) -> None:
    rows = [to_plant_record(CleanRecord.model_validate(r)) for r in read_jsonl(p["clean"])]
    write_jsonl(p["transformed"], rows)
    log.info("transform: %d rows", len(rows))


def cmd_validate_and_load(
    args: argparse.Namespace, p: dict[str, Path]
) -> tuple[list[PlantRecord], list[Rejected], int]:
    rows = list(read_jsonl(p["transformed"]))
    valid, rejected = validate_all(rows)
    exclude = seed_scientific_names(Path(args.seed_file))
    written = write_catalog(Path(args.out), valid, exclude=exclude)
    log.info(
        "validate: %d valid, %d rejected; wrote %d to %s",
        len(valid),
        len(rejected),
        written,
        args.out,
    )
    return [v for v in valid if v.scientific_name not in exclude], rejected, written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pipeline", description=__doc__)
    parser.add_argument(
        "stage", choices=["discover", "extract", "clean", "transform", "load", "run"]
    )
    parser.add_argument("--workdir", default="data/pipeline")
    parser.add_argument("--out", default="data/plants_catalog.json")
    parser.add_argument("--seed-file", default="data/plants_seed.json")
    parser.add_argument("--list-page", action="append", help="Wikipedia list page (repeatable)")
    parser.add_argument("--names-file", help="extra Wikipedia titles, one per line")
    parser.add_argument("--limit", type=int, help="stop extracting after N candidates")
    parser.add_argument("--workers", type=int, default=8, help="parallel extract threads")
    parser.add_argument("--contact", help="contact for the User-Agent (default: PIPELINE_CONTACT)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    workdir = Path(args.workdir)
    p = _paths(workdir)
    http = CachedHttp(workdir / "http_cache", contact=args.contact)

    rejects: list[Rejected] = []
    candidates = 0
    stage = args.stage
    if stage in ("discover", "run"):
        candidates = len(cmd_discover(args, http, p))
    if stage in ("extract", "run"):
        rejects += cmd_extract(args, http, p)
    if stage in ("clean", "run"):
        rejects += cmd_clean(p)
    if stage in ("transform", "run"):
        cmd_transform(p)
    if stage in ("load", "run"):
        final, invalid, _ = cmd_validate_and_load(args, p)
        rejects += invalid
        _write_rejects(p["rejects"], rejects)
        if stage == "run":
            report = build_report(
                candidates=candidates,
                extracted=sum(1 for _ in read_jsonl(p["raw"])),
                cleaned=sum(1 for _ in read_jsonl(p["clean"])),
                rejected=rejects,
                final=final,
                http_stats={"requests": http.requests_made, "cache_hits": http.cache_hits},
            )
            p["report_json"].write_text(json.dumps(report, indent=2))
            p["report_md"].write_text(render_markdown(report))
            log.info("report: %s", p["report_md"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
