import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from scanner import feed_clean, gaps_report, notion_feed, report
from scanner.applied import AppliedKeyError, AppliedSet
from scanner.company_names import display_company, load_overrides, slug_names
from scanner.config import load_config
from scanner.dedupe import canonical_url, dedupe
from scanner.enrich import enrich, fit_distribution
from scanner.filters import Filters
from scanner.models import Job
from scanner.net import PoliteClient
from scanner.pipeline import run_stream
from scanner.score import Scorer
from scanner.sources import ats, feashliaa, simplify
from scanner.state import GapsStore, SeenStore

log = logging.getLogger("scanner")


class UsageError(Exception):
    """Bad configuration or arguments: reported without a traceback, exit code 2."""


def collect(cfg, args, filters, now, since):
    """Run both sources through the phase-1 filters. Returns (jobs, held, stage blocks, feashliaa meta)."""
    src = cfg["sources"]
    jobs, held, blocks, meta = [], [], {}, None
    if src["feashliaa"]["enabled"]:
        data_dir = (Path(args.feashliaa_dir) if args.feashliaa_dir else feashliaa.fetch(src["feashliaa"]))
        meta = feashliaa.read_metadata(data_dir)
        age = feashliaa.check_freshness(meta, src["feashliaa"]["stale_after_hours"], now)
        print(f"Feashliaa last_updated={meta['last_updated']} ({age:.1f} h old), total_jobs={meta['total_jobs']:,}")
        found, stages, hold = run_stream(feashliaa.iter_raw(data_dir), feashliaa.raw_fields, filters, now, since)
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
    return jobs, held, blocks, meta


def print_phase2(result, n_candidates, final, phase2):
    print("\nJD rules:")
    print(f"  candidates entering phase 2      {n_candidates:>10,}")
    print(f"  student-titled jobs held         {result.held_total:>10,} (rescued {result.rescued})")
    print("  drop reasons:")
    for reason, n in sorted(result.drops.items(), key=lambda kv: -kv[1]):
        print(f"    {reason:<30}{n:>8,}")
    if phase2.get("applied"):
        a = phase2["applied"]
        print(f"  skipped, already applied to      {sum(a.values()):>10,} (link {a['url']}, company+title {a['company+title']})")
    print(f"  remaining                        {len(final):>10,}")
    if phase2.get("unresolved_locations"):
        print(f"  kept with an unresolved Workday location {len(phase2['unresolved_locations']):>4,} (listed in the report)")
    if phase2.get("slug_names"):
        print(f"  company names that look like raw slugs {len(phase2['slug_names']):>6,} (listed in the report)")
    print("  JD fetch (source:status):", dict(sorted(result.fetch.items())))
    if result.forbidden:
        print(f"  HTTP 403 tenants ({sum(result.forbidden.values())} jobs, kept without JD if strong title):")
        for host, n in result.forbidden.most_common():
            print(f"    {host:<44}{n:>4}")
    print("  Fit % distribution:")
    for label, n in phase2["fit_dist"]:
        print(f"    {label:<30}{n:>8,}")


def notion_credentials(args):
    token = os.environ.get("NOTION_TOKEN", "").strip()
    ds_id = os.environ.get("NOTION_FEED_DATA_SOURCE_ID", "").strip()
    if not (token and ds_id) and not args.dry_run:
        missing = [n for n, v in (("NOTION_TOKEN", token), ("NOTION_FEED_DATA_SOURCE_ID", ds_id)) if not v]
        raise UsageError(f"{' and '.join(missing)} must be set to write to Notion "
                         "(use --dry-run to scan without it)")
    return token, ds_id


def connect_feed(cfg, token, ds_id, args):
    """(client, feed). Without credentials (dry run only) or with --skip-notion: a virtual feed."""
    names = cfg["notion"]["properties"]
    if args.skip_notion or not (token and ds_id):
        reason = "--skip-notion" if args.skip_notion else "no Notion credentials"
        print(f"Notion: not contacted ({reason}); payloads use the configured schema, "
              "so Feed dedupe and cleanup are skipped")
        return None, notion_feed.virtual_feed(names)
    client = notion_feed.NotionClient(token, cfg["notion"]["api_version"])
    try:
        feed = notion_feed.load_feed(client, ds_id, names)
    except notion_feed.SchemaError as exc:
        raise UsageError(str(exc)) from None
    print(f"Notion: Feed schema OK ({len(names)} properties validated)")
    return client, feed


def notion_step(cfg, args, client, feed, final, seen, overrides, now):
    """Dedupe against seen.json and the Feed, clean up old rows, write the top rows. Returns exit code."""
    ncfg = cfg["notion"]
    cap = args.max_rows if args.max_rows is not None else ncfg["daily_cap"]
    today = now.date()
    feed_links, rows = set(), []
    if client:
        rows = notion_feed.list_rows(client, feed)
        feed_links = {r.link for r in rows if r.link}
        stale = notion_feed.stale_rows(rows, now, ncfg["feed_ttl_days"])
        done = notion_feed.trash_rows(client, stale, dry_run=args.dry_run)
        print(f"Notion: Feed has {len(rows)} rows; {'would trash' if args.dry_run else 'trashed'} {done} "
              f"older than {ncfg['feed_ttl_days']} days with Interested and Apply both unticked")
    candidates = sorted(final, key=lambda j: -j.score)[:args.top] if args.top else final
    if not args.dry_run:
        remembered = [j for j in notion_feed.already_in_feed(candidates, feed_links) if j.key not in seen.seen]
        for job in remembered:
            seen.add(job.key, today)
        if remembered:
            seen.save()
            print(f"Notion: {len(remembered)} jobs already in the Feed recorded in seen.json")
    to_write, skipped = notion_feed.select_new(candidates, seen.seen, feed_links, cap)
    print(f"Notion: {len(to_write)} to write (cap {cap}); skipped {skipped or 'none'}")

    if args.dry_run:
        for job in to_write[:args.show_payloads]:
            page, _ = notion_feed.build_page(job, feed, overrides)
            print(f"\n--- payload (score {job.score:g}; JD shortened for display) ---")
            print(json.dumps(notion_feed.preview_page(page, feed.names["jd"]), indent=2, ensure_ascii=False))
        print("\nDry run: nothing written to Notion; state/seen.json not updated.")
        return 0

    def remember(job, _page_id):
        seen.add(job.key, today)
        seen.save()

    res = notion_feed.write_jobs(client, feed, to_write, overrides, on_written=remember)
    print(f"Notion: wrote {len(res.written)} rows, {len(res.failed)} failed")
    for job, err in res.failed:
        print(f"  FAILED {job.company} | {job.title}: {err}")
    code = 1 if res.failed else 0
    if res.written:
        ok, msg = notion_feed.verify_readback(client, feed, res.written, ncfg["verify_readback_min_chars"])
        print("Notion:", msg)
        code = code or (0 if ok else 1)
    return code


def run(args: argparse.Namespace) -> int:
    started = time.monotonic()
    cfg = load_config(args.config)
    filters = Filters.from_config(cfg)
    now = datetime.now(timezone.utc)
    today = now.date()
    overrides = load_overrides()
    state_dir = Path(args.state_dir or cfg["state"]["dir"])
    seen, gaps = SeenStore(state_dir / "seen.json"), GapsStore(state_dir / "gaps.json")
    use_jd = cfg["jd"]["enabled"] and not args.no_jd
    use_notion = not args.no_notion
    if use_notion and not use_jd and not args.dry_run:
        raise UsageError("writing to Notion needs JD text; drop --no-jd (or use --dry-run / --no-notion)")

    # Fail fast, before the slow scan: credentials and Feed schema.
    client = feed = None
    if use_notion:
        token, ds_id = notion_credentials(args)
        client, feed = connect_feed(cfg, token, ds_id, args)

    fresh = cfg["freshness"]
    since = args.since if args.since is not None else (
        fresh["default_days"] if seen.existed else fresh["first_run_days"])
    print(f"Window: first seen in the last {since:g} day(s)"
          + ("" if args.since is not None or seen.existed else " (first run: no state/seen.json yet)"))

    jobs, held, blocks, meta = collect(cfg, args, filters, now, since)
    deduped, merges = dedupe(jobs)
    blocks["Combined"] = [("before dedupe", len(jobs)), ("merged_url", merges["url"]),
                          ("merged_fuzzy", merges["fuzzy"]), ("after dedupe", len(deduped))]

    try:
        applied = AppliedSet.load_encrypted(cfg["applied"]["path"], os.environ.get("APPLIED_KEY", ""), overrides)
    except AppliedKeyError as exc:
        raise UsageError(str(exc)) from None
    result = phase2 = None
    final, applied_skipped = applied.filter(deduped)
    if use_jd:
        held_deduped, _ = dedupe(held)
        keys = {d.key for d in deduped}
        held_deduped = [h for h in held_deduped if h.key not in keys]
        jdcfg = cfg["jd"]
        client_jd = PoliteClient(delay=jdcfg["request_delay"], per_ats=jdcfg["max_per_ats"])
        scorer = Scorer.load()
        result = enrich(deduped, held_deduped, cfg, filters, scorer, client_jd,
                        progress=lambda m: print(m, flush=True))
        final, applied_skipped = applied.filter(result.kept)
        final_ids = {id(j) for j in final}      # identity: Job has field-wise __eq__
        phase2 = {"drops": result.drops, "fetch": result.fetch, "forbidden": result.forbidden,
                  "held": result.held_total, "rescued": result.rescued, "fit_dist": fit_distribution(final),
                  "applied": {k: len(v) for k, v in applied_skipped.items()},
                  "forbidden_jobs": [(display_company(j, overrides), j.title, j.url) for j in result.forbidden_jobs
                                     if id(j) in final_ids],
                  "unresolved_locations": [(display_company(j, overrides), j.title, j.url) for j in final
                                           if j.location_unresolved],
                  "slug_names": slug_names(final, overrides)}

        # Gaps: every job whose JD was fetched, kept or dropped, once per job.
        added = gaps_report.record_gaps(gaps, deduped + held_deduped, scorer, today, overrides)
        gaps.prune(today, cfg["state"]["prune_days"])
        gaps.save()
        print(f"Gaps: recorded {added} new jobs ({len(gaps.jobs)} in state/gaps.json)")
        if args.gaps_report == "always" or (args.gaps_report == "auto" and gaps_report.is_report_day(today)):
            path = cfg["gaps"]["report_path"]
            report.write(path, gaps_report.render(gaps, today))
            print(f"Gaps: wrote {path}")

    for name, stages in blocks.items():
        print(report.format_counts(name, stages))
    if result:
        print_phase2(result, len(deduped), final, phase2)
    out_path = args.out or cfg["report"]["path"]
    report.write(out_path, report.render(final, since, now, meta, blocks, len(deduped), phase2))
    print(f"\nWrote {out_path} ({len(final)} jobs) in {time.monotonic() - started:.0f}s")
    if args.sample:
        print(f"\n{min(args.sample, len(final))} random rows (seed {args.seed}):")
        print(report.format_sample(report.sample_rows(final, args.sample, args.seed)))

    code = 0
    if use_notion and use_jd:
        seen.prune(today, cfg["state"]["prune_days"])
        code = notion_step(cfg, args, client, feed, final, seen, overrides, now)
        if not args.dry_run:
            seen.save()
    return code


def run_clean_feed(args: argparse.Namespace) -> int:
    """Apply the current rules to the rows already in the Feed (see scanner.feed_clean)."""
    cfg = load_config(args.config)
    filters = Filters.from_config(cfg)
    token = os.environ.get("NOTION_TOKEN", "").strip()
    ds_id = os.environ.get("NOTION_FEED_DATA_SOURCE_ID", "").strip()
    if not (token and ds_id):
        raise UsageError("NOTION_TOKEN and NOTION_FEED_DATA_SOURCE_ID must be set to read the Feed")
    client = notion_feed.NotionClient(token, cfg["notion"]["api_version"])
    try:
        feed = notion_feed.load_feed(client, ds_id, cfg["notion"]["properties"])
    except notion_feed.SchemaError as exc:
        raise UsageError(str(exc)) from None
    items = feed_clean.list_items(client, feed)
    print(f"Feed: {len(items)} rows read", flush=True)
    locations = feed_clean.find_locations({i.link for i in items}, cfg, args.feashliaa_dir)
    print(f"Locations found in the source data for {len(locations)} of {len(items)} rows", flush=True)

    # Workday "N Locations": read the real list from the job detail, as a new job would be.
    jd_locations: dict[str, list[str]] = {}
    pending = [i for i in items if filters.location_status(locations.get(canonical_url(i.link), ""))[0] == "unresolved"]
    if pending:
        http = PoliteClient(delay=cfg["jd"]["request_delay"], per_ats=cfg["jd"]["max_per_ats"])
        for item in pending:
            probe = Job(company=item.company, title=item.title, location="", url=item.link, source=item.source,
                        first_seen=datetime.now(timezone.utc))
            ref = ats.locate(probe)
            try:
                jd_locations[canonical_url(item.link)] = ats.fetch_full(ref, http).locations if ref else []
            except Exception as exc:                      # unresolved rows are kept, not guessed
                log.debug("location lookup failed: %s", exc)
    rep = feed_clean.clean(items, locations, cfg, filters, Scorer.load(), jd_locations)
    print(feed_clean.render(rep, dry_run=args.dry_run))
    if not args.dry_run:
        done = feed_clean.trash(client, rep, dry_run=False)
        print(f"Trashed {done} rows")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scanner")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run", help="scan sources, score, and write the best new jobs to the Notion Feed")
    p.add_argument("--since", type=float,
                   help="only jobs first seen in the last N days (default: 3, or 14 on the first run)")
    p.add_argument("--dry-run", action="store_true",
                   help="no Notion writes or trashing, no seen.json update; print what would be written")
    p.add_argument("--max-rows", type=int, help="rows to write this run (default notion.daily_cap)")
    p.add_argument("--top", type=int,
                   help="testing aid: only consider the N highest-scoring jobs (so a rerun repeats the same set)")
    p.add_argument("--show-payloads", type=int, default=5, help="dry run: print the first N Notion payloads")
    p.add_argument("--no-notion", action="store_true", help="scan and report only; skip Notion entirely")
    p.add_argument("--skip-notion", action="store_true",
                   help="dry run without contacting Notion (no schema check, no Feed dedupe)")
    p.add_argument("--gaps-report", choices=["auto", "always", "never"], default="auto",
                   help="write reports/gaps.md: auto = Mondays (UTC) only")
    p.add_argument("--state-dir", help="where seen.json and gaps.json live (default state/)")
    p.add_argument("--config", help="path to config.yaml")
    p.add_argument("--out", help="report path (default from config)")
    p.add_argument("--feashliaa-dir", help="use this local job-board-data clone as-is (no git sync)")
    p.add_argument("--no-jd", action="store_true", help="phase 1 only: skip JD fetch, rules and scoring")
    p.add_argument("--sample", type=int, default=0, help="print N random result rows")
    p.add_argument("--seed", type=int, default=None, help="seed for --sample")
    p.add_argument("--simplify-file", help="use a local listings.json instead of downloading")
    c = sub.add_parser("clean-feed", help="apply the current rules to the rows already in the Notion Feed "
                                         "and trash the ones that fail (never a row with Interested / Apply ticked)")
    c.add_argument("--dry-run", action="store_true", help="report what would be trashed; trash nothing")
    c.add_argument("--config", help="path to config.yaml")
    c.add_argument("--feashliaa-dir", help="use this local job-board-data clone as-is (no git sync)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        return run_clean_feed(args) if args.cmd == "clean-feed" else run(args)
    except UsageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
