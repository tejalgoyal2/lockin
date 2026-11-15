import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from scanner import audit as audit_mod
from scanner import boards as boards_mod
from scanner import discover, gaps_report, notion_feed, report
from scanner.applied import AppliedKeyError, AppliedSet
from scanner.ats_names import NameStore, fill_names
from scanner.company_names import display_company, load_overrides, slug_names
from scanner.config import load_config
from scanner.dedupe import dedupe
from scanner.enrich import enrich, rating_distribution
from scanner.feedback import FeedbackStore
from scanner.filters import Filters
from scanner.net import client_from_config
from scanner.pipeline import run_stream
from scanner.score import Scorer
from scanner.sources import (ats, baystreet, bcgov, builtin, feashliaa, googlejobs, hn, jobbank, jobhive, jobicy, listing,
                             simplify, sitesearch, supplied, watchlist, yc)
from scanner.state import GapsStore, SeenStore

log = logging.getLogger("scanner")


class UsageError(Exception):
    """Bad configuration or arguments: reported without a traceback, exit code 2."""


def collect(cfg, args, filters, now, since, store=None):
    """Run every source through the phase-1 filters. Returns (jobs, held, stage blocks, feashliaa meta)."""
    src = cfg["sources"]
    jobs, held, blocks, meta = [], [], {}, None
    if src["feashliaa"]["enabled"]:
        data_dir = (Path(args.feashliaa_dir) if args.feashliaa_dir else feashliaa.fetch(src["feashliaa"]))
        meta = feashliaa.read_metadata(data_dir)
        age = feashliaa.check_freshness(meta, src["feashliaa"]["stale_after_hours"], now)
        print(f"Feashliaa last_updated={meta['last_updated']} ({age:.1f} h old), total_jobs={meta['total_jobs']:,}")
        found, stages, hold = run_stream(feashliaa.iter_raw(data_dir), feashliaa.raw_fields, filters, now, since,
                                         tap=store.tap if store else None)
        jobs += found
        held += hold
        blocks["Feashliaa"] = stages
    if src["simplify"]["enabled"]:
        listings = (simplify.load_file(Path(args.simplify_file)) if args.simplify_file
                    else simplify.fetch(src["simplify"]))
        cats = src["simplify"]["categories"]
        found, stages, hold = run_stream(
            simplify.iter_raw(listings), simplify.raw_fields, filters, now, since,
            pre_stages=(("active_category", lambda r: simplify.is_listed(r, cats)),), tap=store.tap if store else None)
        jobs += found
        held += hold
        blocks["Simplify"] = stages
    wl = src.get("watchlist") or {}
    if wl.get("enabled"):
        try:
            boards = watchlist.load_boards(wl["path"], *wl.get("extra_paths", []))
        except watchlist.WatchlistError as exc:
            raise UsageError(str(exc)) from None
        today = now.date()
        resting = [b for b in boards if store and not store.due(b, today)]
        if resting:
            boards = [b for b in boards if b not in resting]
            print(f"Watchlist: {len(resting)} boards resting after {boards_mod.MAX_FAILS} failed reads in a row "
                  f"(retried later; see reports/boards.md)")
        records, failed = watchlist.fetch_all(
            boards, client_from_config(cfg["jd"]),
            workers=wl.get("workers", 6), now=now, seed_ashby=ats.seed_ashby_board,
            want=lambda t, where: (bool(filters.title_tier(t)) and filters.title_not_excluded(t)
                                   and (not where or filters.location_status(where)[0] != "not_canada")))
        if store:
            failed_by = {boards_mod.key(b): err for b, err in failed}
            for b in boards:
                store.record_read(b, failed_by.get(boards_mod.key(b), ""), today)
        print(f"Watchlist: {len(boards) - len(failed)} of {len(boards)} boards read, {len(records):,} postings"
              + (f"; not read: {', '.join(f'{b.ats}/{b.slug}' for b, _ in failed)}" if failed else ""))
        if store:
            store.watched = boards_mod.known_keys(boards + resting)
        found, stages, hold = run_stream(iter(records), watchlist.raw_fields, filters, now, since)
        jobs += found
        held += hold
        blocks["Watchlist"] = stages
    http = client_from_config(cfg["jd"])
    sites = [      # (label, config key, reader(config) -> records, pre-stages)
        ("YC", "yc", lambda c: yc.fetch(c["pages"], http, now),
         (("experience_ok", lambda r: yc.experience_ok(r, cfg["jd"]["max_years"] + 1)),)),
        ("Built In", "builtin", lambda c: builtin.fetch(c["base_url"], c["paths"], c["max_pages"], http, now, since), ()),
        ("Bay Street Oracle", "baystreet",
         lambda c: baystreet.fetch(c["sitemap_url"], http, now, lambda words: filters.title_tier(words) is not None,
                                   lambda url: ats.detect_ats(url) in ats.SUPPORTED), ()),
        ("HN", "hn", lambda c: hn.fetch(http, now), ()),
        ("Job Bank", "jobbank",
         lambda c: jobbank.fetch(http, c.get("nocs", []), c.get("terms", []),
                                 since if getattr(args, "since", None) is not None else min(since, c.get("max_days", 3)),
                                 max_pages=c.get("max_pages", jobbank.MAX_PAGES),
                                 max_queries=c.get("max_queries", jobbank.MAX_QUERIES)), ()),
        ("BC Gov", "bcgov", lambda c: bcgov.fetch(http), ()),
        ("Jobicy", "jobicy", lambda c: jobicy.fetch(http, now), ()),
        ("Google Jobs", "googlejobs",
         lambda c: googlejobs.fetch(http, c.get("terms", []), c.get("location", "Canada"), now,
                                    per_run=c.get("queries_per_run", googlejobs.QUERIES_PER_RUN),
                                    prefer=lambda url: ats.detect_ats(url) in ats.SUPPORTED and ats.detect_ats(url) not in ats.SITES), ()),
        ("Site Search", "sitesearch",
         lambda c: sitesearch.fetch(http, c.get("hosts", []), c.get("titles", []), c.get("where", ""), now,
                                    brave_per_run=c.get("brave_per_run", 15), serpapi_per_run=c.get("serpapi_per_run", 2)), ()),
        ("Jobhive", "jobhive", lambda c: jobhive.fetch(c.get("systems") or jobhive.SYSTEMS, since, now), ()),
    ]
    for label, key, read, pre_stages in sites:
        conf = src.get(key) or {}
        if not conf.get("enabled"):
            continue
        try:
            records = read(conf)
        except Exception as exc:       # a site that is down or changed must not stop the run: say so and go on
            print(f"WARNING {label}: not read ({type(exc).__name__}: {str(exc)[:120]})")
            continue
        print(f"{label}: {len(records):,} postings")
        for rec in records:
            supplied.put(rec["url"], rec.get("jd", ""))       # a description the source handed over (Google for Jobs, Jobhive)
        found, stages, hold = run_stream(iter(records), listing.raw_fields, filters, now, since, pre_stages=pre_stages)
        jobs += found
        held += hold
        blocks[label] = stages
    return jobs, held, blocks, meta


def boards_step(cfg, store, filters, now, *, dry_run: bool) -> None:
    """Board memory after the scan: read the busiest boards the scan met that are not on the watchlist, add the ones
    with Canadian postings to watchlist.auto.yaml, write reports/boards.md and save state/boards.json."""
    wl = cfg["sources"]["watchlist"]
    auto = wl.get("auto") or {}
    added = []
    if auto.get("enabled") and wl.get("extra_paths"):
        added = boards_mod.promote(store, client_from_config(cfg["jd"]), filters, now, auto_path=wl["extra_paths"][0],
                                   limit=auto.get("per_run", 12), auto_max=auto.get("max_boards", 150), dry_run=dry_run,
                                   min_candidates=auto.get("min_candidates", 2),
                                   skip=re.compile(auto["skip_pattern"], re.IGNORECASE) if auto.get("skip_pattern") else None)
    verb = "would add" if dry_run else "added"
    print(f"Boards: {verb} {len(added)} new employer boards to {wl['extra_paths'][0] if wl.get('extra_paths') else 'nothing'}"
          + "".join(f"\n  + {boards_mod.key(f.board)} ({f.name}): {h.canadian} Canadian postings, {h.candidates} pass the title filters" for f, h in added))
    if not dry_run:
        store.save()
        report.write(auto.get("report_path", "reports/boards.md"), boards_mod.render(store, added, now.date()))


def print_phase2(result, n_candidates, final, phase2):
    print("\nJD rules:")
    print(f"  candidates entering phase 2      {n_candidates:>10,} ({result.held_total} intern / co-op titles)")
    print("  drop reasons:")
    for reason, n in sorted(result.drops.items(), key=lambda kv: -kv[1]):
        print(f"    {reason:<38}{n:>8,}")
    if phase2.get("applied"):
        a = phase2["applied"]
        print(f"  skipped, already applied to      {sum(a.values()):>10,} (link {a['url']}, company+title {a['company+title']})")
    print(f"  kept                             {len(final):>10,}")
    if phase2.get("unresolved_locations"):
        print(f"  kept with an unresolved Workday location {len(phase2['unresolved_locations']):>4,} (listed in the report)")
    if phase2.get("slug_names"):
        print(f"  company names that look like raw slugs {len(phase2['slug_names']):>6,} (listed in the report)")
    print("  JD fetch (source:status):", dict(sorted(result.fetch.items())))
    print("  Scores:", ", ".join(f"{label} = {n}" for label, n in phase2["ratings"]))


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


def trash_moved_rows(client, feed, *, dry_run: bool, feedback=None, feedback_report=None) -> int:
    """Start of the run: trash the Feed rows the owner's automation has copied to the main database (`done-del`
    ticked). No other row is ever trashed by a run. Each trashed row is first counted in `feedback` (what was
    picked, by Fit band, source and tags); a dry run counts nothing."""
    rows = notion_feed.list_rows(client, feed)
    moved = notion_feed.moved_rows(rows)
    done = notion_feed.trash_rows(client, moved, dry_run=dry_run, on_trashed=feedback.record if feedback else None)
    if feedback and feedback.dirty:
        feedback.save()
        if feedback_report:
            report.write(feedback_report, feedback.render())
    print(f"Notion: Feed has {len(rows)} rows; {'would trash' if dry_run else 'trashed'} {done} with done-del ticked")
    return done


def notion_step(cfg, args, client, feed, final, seen, overrides, now):
    """Dedupe against seen.json and the Feed, write the top rows. Returns exit code."""
    ncfg = cfg["notion"]
    cap = args.max_rows if args.max_rows is not None else ncfg["daily_cap"]
    today = now.date()
    feed_links, rows = set(), []
    if client:
        rows = notion_feed.list_rows(client, feed)
        feed_links = {r.link for r in rows if r.link}
    candidates = final[:args.top] if args.top else final          # `final` is best first
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
            print("\n--- payload (JD shortened for display) ---")
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
        if client:
            trash_moved_rows(client, feed, dry_run=args.dry_run,
                             feedback=FeedbackStore(state_dir / "feedback.json"),
                             feedback_report=cfg["feedback"]["report_path"])

    fresh = cfg["freshness"]
    since = args.since if args.since is not None else (
        fresh["default_days"] if seen.existed else fresh["first_run_days"])
    print(f"Window: first seen in the last {since:g} day(s)"
          + ("" if args.since is not None or seen.existed else " (first run: no state/seen.json yet)"))

    store = (boards_mod.BoardStore(state_dir / "boards.json",
                                   relevant=lambda t: filters.title_tier(t) is not None and filters.title_not_excluded(t))
             if cfg["sources"].get("watchlist", {}).get("enabled") else None)
    jobs, held, blocks, meta = collect(cfg, args, filters, now, since, store)
    if store:
        boards_step(cfg, store, filters, now, dry_run=args.dry_run)
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
        # Intern / co-op titles go through the same rules (a posting that requires enrollment is dropped there).
        held_deduped, _ = dedupe(held)
        keys = {d.key for d in deduped}
        deduped += [h for h in held_deduped if h.key not in keys]
        # A job already written to the Feed (seen.json) is skipped when the Feed rows are chosen, so its description is
        # not fetched or rated again. (A Workday "N Locations" job gets its final key only after its detail is read,
        # so it is not skipped here.)
        n_all = len(deduped)
        deduped = [d for d in deduped if d.key not in seen.seen]
        print(f"Already in the Feed (state/seen.json): {n_all - len(deduped):,} of {n_all:,} candidates skipped, "
              f"{len(deduped):,} new", flush=True)
        jdcfg = cfg["jd"]
        client_jd = client_from_config(jdcfg)
        scorer = Scorer.load()
        result = enrich(deduped, cfg, filters, scorer, client_jd, progress=lambda m: print(m, flush=True))
        final, applied_skipped = applied.filter(result.kept)
        names = NameStore(state_dir / "company_names.json")
        named = fill_names(final, overrides, names, client_jd, today)
        names.save()
        print(f"Company names from the ATS: {named} jobs", flush=True)
        phase2 = {"drops": result.drops, "fetch": result.fetch, "ratings": rating_distribution(final),
                  "applied": {k: len(v) for k, v in applied_skipped.items()},
                  "unresolved_locations": [(display_company(j, overrides), j.title, j.url) for j in final
                                           if j.location_unresolved],
                  "slug_names": slug_names(final, overrides)}

        # Gaps: every job whose JD was fetched, kept or dropped, once per job.
        added = gaps_report.record_gaps(gaps, deduped, scorer, today, overrides)
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
    if result and (not args.dry_run or args.out):         # a dry run writes it only next to an explicit --out
        dropped_path = str(Path(args.out).with_name("dropped.md")) if args.out else cfg["report"].get("dropped_path", "reports/dropped.md")
        report.write(dropped_path, report.render_dropped(result.dropped))
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


def run_wipe_feed(args: argparse.Namespace) -> int:
    """A fresh start: trash every Feed row the owner has not touched (no box ticked). Notion keeps trashed rows for 30
    days. Follow it with an emptied state/seen.json and a scan, and the Feed is rebuilt under the current rules."""
    cfg = load_config(args.config)
    token = os.environ.get("NOTION_TOKEN", "").strip()
    ds_id = os.environ.get("NOTION_FEED_DATA_SOURCE_ID", "").strip()
    if not (token and ds_id):
        raise UsageError("NOTION_TOKEN and NOTION_FEED_DATA_SOURCE_ID must be set to read the Feed")
    client = notion_feed.NotionClient(token, cfg["notion"]["api_version"])
    try:
        feed = notion_feed.load_feed(client, ds_id, cfg["notion"]["properties"])
    except notion_feed.SchemaError as exc:
        raise UsageError(str(exc)) from None
    rows = notion_feed.list_rows(client, feed)
    untouched = [r for r in rows if r.untouched]
    print(f"Feed: {len(rows)} rows, {len(rows) - len(untouched)} with a box ticked (kept)")
    done = notion_feed.trash_rows(client, untouched, dry_run=args.dry_run)
    print(f"{'Would trash' if args.dry_run else 'Trashed'} {done} rows")
    return 0


def run_discover(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    filters, now = Filters.from_config(cfg), datetime.now(timezone.utc)
    client = client_from_config(cfg["jd"])
    if not (args.names or args.workday or args.phenom or args.successfactors or args.icims):
        raise UsageError("give at least one company name, --workday TENANT[.WDN/SITE] or --phenom HOST/COUNTRY/LANG")
    for item in args.workday:
        slug, _, name = item.partition("=")
        slug = slug.strip()
        if "/" not in slug:                         # just the tenant ("rbc" or "rbc.wd3"): its robots.txt names the sites
            tenant = slug.split(".")[0]
            hits = discover.probe_workday_tenant(tenant, name.strip(), client, filters, now)
            print("\n".join(h.yaml_line() for h in hits) if hits else f"# {slug}: no Workday tenant with postings found")
            continue
        try:
            hit = discover.probe_workday(slug, name.strip(), client, filters, now)
        except Exception as exc:
            print(f"# {slug}: not read ({type(exc).__name__}: {str(exc)[:100]})")
            continue
        print(hit.yaml_line() if hit else f"# {slug}: no postings")
    for probe_one, items in ((discover.probe_phenom, args.phenom), (discover.probe_successfactors, args.successfactors),
                                (discover.probe_icims, args.icims)):
        for item in items:
            slug, _, name = item.partition("=")
            try:
                hit = probe_one(slug.strip(), name.strip(), client, filters, now)
            except Exception as exc:
                print(f"# {slug}: not read ({type(exc).__name__}: {str(exc)[:100]})")
                continue
            print(hit.yaml_line() if hit else f"# {slug}: no postings")
    for name in args.names:
        hits = discover.probe(name, client, filters, now, slugs=[args.slug] if args.slug else None)
        if not hits:
            print(f"# {name}: no Workday / Greenhouse / Lever / Ashby / SmartRecruiters board found "
                  "(try --slug, --workday TENANT, or --phenom HOST/ca/en for a Phenom careers site)")
        for hit in hits:
            print(hit.yaml_line())
    return 0


def run_audit(args: argparse.Namespace) -> int:
    """Which phase-1 rule stops the fresh Canadian postings, and which discarded titles look wrong (scanner.audit)."""
    from datetime import timedelta
    cfg = load_config(args.config)
    filters, now = Filters.from_config(cfg), datetime.now(timezone.utc)
    src, audit = cfg["sources"], audit_mod.Audit(filters, now, args.days)
    cutoff = (now - timedelta(days=args.days)).isoformat()
    if src["feashliaa"]["enabled"]:
        data_dir = Path(args.feashliaa_dir) if args.feashliaa_dir else feashliaa.fetch(src["feashliaa"])
        for rec in feashliaa.iter_raw(data_dir):
            if (rec.get("first_seen") or "") < cutoff:              # ISO strings compare in time order: skip old rows unparsed
                audit.funnel["records"] += 1
                continue
            audit.add(feashliaa.raw_fields(rec))
    if src["simplify"]["enabled"]:
        for rec in simplify.fetch(src["simplify"]):
            if simplify.is_listed(rec, src["simplify"]["categories"]):
                audit.add(simplify.raw_fields(rec))
    wl = src.get("watchlist") or {}
    if wl.get("enabled"):
        boards = watchlist.load_boards(wl["path"], *wl.get("extra_paths", []))
        records, _ = watchlist.fetch_all(boards, client_from_config(cfg["jd"]), workers=wl.get("workers", 6), now=now,
                                         seed_ashby=ats.seed_ashby_board)
        for rec in records:
            audit.add(watchlist.raw_fields(rec))
    text = audit.render()
    report.write(args.out, text)
    print(text)
    print(f"Wrote {args.out}")
    return 0


def run_why(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    filters, now = Filters.from_config(cfg), datetime.now(timezone.utc)
    since = args.since if args.since is not None else cfg["freshness"]["default_days"]
    client = client_from_config(cfg["jd"])
    wanted = args.name.lower()
    wl = cfg["sources"]["watchlist"]
    boards = [b for b in watchlist.load_boards(wl["path"], *wl.get("extra_paths", []))
              if wanted in (b.name or "").lower() or wanted in b.slug.lower()]
    if boards:
        print(f"On the watchlist: {', '.join(f'{b.ats}/{b.slug}' for b in boards)}")
        hits = [discover.Hit(b, 0, 0, 0) for b in boards]
    else:
        print(f"{args.name}: not on the watchlist, so only the Feashliaa dataset (if it carries them) could have "
              "found a posting. Looking for a board ...")
        hits = discover.probe(args.name, client, filters, now)
        for h in hits:
            print("  found:", h.yaml_line().strip())
        if not hits:
            print("  no board found: try `discover --slug` or `discover --workday`")
            return 0
    print(discover.why(hits, client, filters, now, since))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scanner")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run", help="scan sources, score, and write the best new jobs to the Notion Feed")
    p.add_argument("--since", type=float,
                   help="only jobs first seen in the last N days (default: freshness.default_days)")
    p.add_argument("--dry-run", action="store_true",
                   help="no Notion writes or trashing, no seen.json update; print what would be written")
    p.add_argument("--max-rows", type=int, help="rows to write this run (default notion.daily_cap)")
    p.add_argument("--top", type=int,
                   help="testing aid: only consider the N best jobs (so a rerun repeats the same set)")
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
    c = sub.add_parser("wipe-feed", help="trash every Feed row with no box ticked (a fresh start; Notion keeps them 30 days)")
    c.add_argument("--dry-run", action="store_true", help="count the rows; trash nothing")
    c.add_argument("--config", help="path to config.yaml")
    d = sub.add_parser("discover", help="find the job board of an employer (Greenhouse, Lever, Ashby, SmartRecruiters, "
                                        "Workday) and print the watchlist.yaml line; writes nothing")
    d.add_argument("names", nargs="*", help="company names")
    d.add_argument("--workday", action="append", default=[], metavar="TENANT[.WDN/SITE][=Name]",
                   help="check a Workday tenant: generalmotors.wd5/Careers_GM=General Motors, or just the tenant name (generalmotors) "
                        "to find its career sites")
    d.add_argument("--phenom", action="append", default=[], metavar="HOST/COUNTRY/LANG[=Name]",
                   help="check a Phenom People careers site (jobs.bell.ca/ca/en=Bell): Bell, Air Canada, OpenText ...")
    d.add_argument("--successfactors", action="append", default=[], metavar="HOST[=Name]",
                   help="check a SuccessFactors careers site (careers.telus.com=Telus): Telus, Scotiabank, Rogers ...")
    d.add_argument("--icims", action="append", default=[], metavar="HOST[=Name]",
                   help="check an iCIMS careers site (careersen-victoria.icims.com=City of Victoria): cities, hospitals ...")
    d.add_argument("--slug", help="try this exact board slug instead of guessing from the name (one company)")
    d.add_argument("--config", help="path to config.yaml")
    a = sub.add_parser("audit", help="which filter rules stop the fresh Canadian postings, which discarded titles look "
                                     "like jobs you want, and which title words the filter does not know; writes reports/audit.md")
    a.add_argument("--days", type=float, default=14, help="postings first seen in the last N days (default 14)")
    a.add_argument("--out", default="reports/audit.md", help="where to write the report")
    a.add_argument("--config", help="path to config.yaml")
    a.add_argument("--feashliaa-dir", help="use this local job-board-data clone as-is (no git sync)")
    w = sub.add_parser("why", help="read an employer's board live and say, per Canadian posting, which rule keeps "
                                   "it out of the Feed (or that nothing does)")
    w.add_argument("name", help="company name of a watchlist entry, or a name to discover")
    w.add_argument("--since", type=float, help="freshness window in days (default from config.yaml)")
    w.add_argument("--config", help="path to config.yaml")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        return {"wipe-feed": run_wipe_feed, "discover": run_discover, "why": run_why,
                "audit": run_audit}.get(args.cmd, run)(args)
    except UsageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
