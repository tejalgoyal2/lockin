import argparse
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from scanner import report
from scanner.enrich import enrich, fit_distribution
from scanner.net import PoliteClient
from scanner.score import Scorer
from scanner.config import load_config
from scanner.filters import Filters
from scanner.dedupe import dedupe
from scanner.pipeline import run_stream
from scanner.sources import feashliaa, simplify


def run(args: argparse.Namespace) -> int:
    started = time.monotonic()
    cfg = load_config(args.config)
    filters = Filters.from_config(cfg)
    now = datetime.now(timezone.utc)
    since = args.since if args.since is not None else cfg["freshness"]["default_days"]
    src = cfg["sources"]

    jobs, held, blocks, meta = [], [], {}, None

    if src["feashliaa"]["enabled"]:
        data_dir = (Path(args.feashliaa_dir) if args.feashliaa_dir
                    else feashliaa.fetch(src["feashliaa"]))
        meta = feashliaa.read_metadata(data_dir)
        age = feashliaa.check_freshness(meta, src["feashliaa"]["stale_after_hours"], now)
        print(f"Feashliaa last_updated={meta['last_updated']} ({age:.1f} h old), "
              f"total_jobs={meta['total_jobs']:,}")
        found, stages, hold = run_stream(
            feashliaa.iter_raw(data_dir), feashliaa.raw_fields, filters, now, since)
        jobs += found
        held += hold
        blocks["Feashliaa"] = stages

    if src["simplify"]["enabled"]:
        listings = (simplify.load_file(Path(args.simplify_file)) if args.simplify_file
                    else simplify.fetch(src["simplify"]))
        cats = src["simplify"]["categories"]
        found, stages, hold = run_stream(
            simplify.iter_raw(listings), simplify.raw_fields, filters, now, since,
            pre_stages=(("active_category", lambda r: simplify.is_listed(r, cats)),))
        jobs += found
        held += hold
        blocks["Simplify"] = stages

    deduped, merges = dedupe(jobs)
    blocks["Combined"] = [("before dedupe", len(jobs)), ("merged_url", merges["url"]),
                          ("merged_fuzzy", merges["fuzzy"]), ("after dedupe", len(deduped))]

    result, phase2 = None, None
    use_jd = cfg["jd"]["enabled"] and not args.no_jd
    if use_jd:
        held_deduped, _ = dedupe(held)
        held_deduped = [h for h in held_deduped if h.key not in {d.key for d in deduped}]
        jdcfg = cfg["jd"]
        client = PoliteClient(delay=jdcfg["request_delay"], per_ats=jdcfg["max_per_ats"])
        result = enrich(deduped, held_deduped, cfg, filters, Scorer.load(), client,
                        progress=lambda m: print(m, flush=True))
        phase2 = {"drops": result.drops, "fetch": result.fetch, "held": result.held_total,
                  "rescued": result.rescued, "fit_dist": fit_distribution(result.kept)}
        final = result.kept
    else:
        final = deduped

    for name, stages in blocks.items():
        print(report.format_counts(name, stages))
    if result:
        print("\nJD rules:")
        print(f"  candidates entering phase 2      {len(deduped):>10,}")
        print(f"  student-titled jobs held         {result.held_total:>10,} (rescued {result.rescued})")
        print("  drop reasons:")
        for reason, n in sorted(result.drops.items(), key=lambda kv: -kv[1]):
            print(f"    {reason:<30}{n:>8,}")
        print(f"  remaining                        {len(final):>10,}")
        print("  JD fetch (source:status):", dict(sorted(result.fetch.items())))
        print("  Fit % distribution:")
        for label, n in phase2["fit_dist"]:
            print(f"    {label:<30}{n:>8,}")
    out_path = args.out or cfg["report"]["path"]
    report.write(out_path, report.render(final, since, now, meta, blocks, len(deduped), phase2))
    print(f"\nWrote {out_path} ({len(final)} jobs) in {time.monotonic() - started:.0f}s")
    if args.sample:
        print(f"\n{min(args.sample, len(final))} random rows (seed {args.seed}):")
        print(report.format_sample(report.sample_rows(final, args.sample, args.seed)))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scanner")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run", help="scan sources and write the candidate report")
    p.add_argument("--since", type=float, help="only jobs first seen in the last N days")
    p.add_argument("--dry-run", action="store_true",
                   help="no external writes (phase 1 has none; flag reserved for Notion)")
    p.add_argument("--config", help="path to config.yaml")
    p.add_argument("--out", help="report path (default from config)")
    p.add_argument("--feashliaa-dir", help="use this local job-board-data clone as-is (no git sync)")
    p.add_argument("--no-jd", action="store_true", help="phase 1 only: skip JD fetch, rules and scoring")
    p.add_argument("--sample", type=int, default=0, help="print N random result rows")
    p.add_argument("--seed", type=int, default=None, help="seed for --sample")
    p.add_argument("--simplify-file", help="use a local listings.json instead of downloading")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    return run(args)
