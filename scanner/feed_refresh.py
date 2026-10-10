"""Give Feed rows that were written without a JD their live posting (SPEC §11 #59).

A Workday row with the `jd unavailable` Signal usually holds a requisition id Workday no longer serves
(the posting was re-issued under a new number). For each such row: ask the tenant's job search for the same
title (exact, normalised), take a Canadian match, fetch its JD, and update Link, JD, Fit % and Signals in
place. Name, Company, Source and the Interested / Apply checkboxes are never touched. No match: the row is
left as it is and listed.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone

from scanner import enrich as enrich_mod
from scanner import notion_feed
from scanner.cluster import ClusterResolver
from scanner.dedupe import canonical_url
from scanner.feed_clean import FeedItem
from scanner.filters import Filters
from scanner.models import Job
from scanner.score import Scorer
from scanner.sources import ats

UNAVAILABLE = "jd unavailable"


@dataclass
class RefreshReport:
    updated: list[tuple[FeedItem, str]] = field(default_factory=list)   # (row, link it now has)
    no_match: list[FeedItem] = field(default_factory=list)
    duplicate: list[FeedItem] = field(default_factory=list)             # the live link is already another row
    locations: dict[str, tuple[str, str]] = field(default_factory=dict)  # canonical link -> (location, "ats")


def stale_rows(items: list[FeedItem]) -> list[FeedItem]:
    return [i for i in items if i.source == "Workday" and UNAVAILABLE in i.signals]


def refresh(client, feed: notion_feed.Feed, items: list[FeedItem], cfg: dict, filters: Filters, scorer: Scorer,
            http, *, dry_run: bool) -> RefreshReport:
    rep = RefreshReport()
    clusters = ClusterResolver(cfg)
    is_canadian = lambda where: filters.location_status(where)[0] in ("ok", "quebec_only")
    taken = {canonical_url(i.link): i for i in items}
    for item in stale_rows(items):
        probe = Job(company=item.company, title=item.title, location="", url=item.link, source=item.source,
                    first_seen=datetime.now(timezone.utc), sources={item.source})
        ref = ats.locate(probe)
        if ref is None:
            rep.no_match.append(item)
            continue
        live_url, detail = item.link, None
        try:
            detail = ats.fetch_full(ref, http)             # the old id may be back
        except Exception as exc:
            if enrich_mod._workday_gone(exc):
                twin = ats.relocate_workday(ref, item.title, http, is_canadian)
                if twin is not None:
                    live_url, detail = twin.url, twin.detail
        if detail is None or not detail.text.strip():
            rep.no_match.append(item)
            continue
        clash = taken.get(canonical_url(live_url))
        if clash is not None and clash is not item:
            rep.duplicate.append(item)
            continue
        job = Job(company=item.company, company_name=item.company, title=item.title, location="", url=live_url,
                  source=item.source, first_seen=datetime.now(timezone.utc), sources={item.source}, jd=detail.text,
                  jd_status="ok", jd_locations=detail.locations,
                  weak_title=filters.title_tier(item.title) == "weak")
        where = " ; ".join(detail.locations)
        status, part = filters.location_status(where) if where else ("", None)
        job.location = part or where or "Canada"
        enrich_mod.apply_rules(job, cfg, filters, scorer, clusters)      # fills Fit %, matched, Signals
        props, _ = notion_feed.build_properties(job, feed, {})
        n = feed.names
        patch = {k: props[k] for k in (n["link"], n["jd"], n["fit"], n["signals"])}
        if not dry_run:
            client.request("PATCH", f"/v1/pages/{item.page_id}", json={"properties": patch})
        taken.pop(canonical_url(item.link), None)
        item.link, item.jd, item.signals = live_url, detail.text, list(job.signals)
        taken[canonical_url(live_url)] = item
        rep.updated.append((item, live_url))
        if part:
            rep.locations[canonical_url(live_url)] = (part, "ats")
    return rep


def render(rep: RefreshReport, *, dry_run: bool) -> str:
    out = [f"Feed rows without a JD ({'dry run: nothing changed' if dry_run else 'updated in place'}): "
           f"{len(rep.updated)} found a live posting, {len(rep.no_match)} did not, {len(rep.duplicate)} duplicate"]
    if rep.updated:
        out += ["", "| Company | Role | Live link |", "|---|---|---|"]
        out += [f"| {i.company} | {i.title} | {link} |" for i, link in rep.updated]
    if rep.no_match:
        out += ["", "Still `jd unavailable` (no exact title match in a Canadian location), row left as it is:", "",
                "| Company | Role | Link |", "|---|---|---|"]
        out += [f"| {i.company} | {i.title} | {i.link} |" for i in rep.no_match]
    if rep.duplicate:
        out += ["", "Live link is already another Feed row, row left as it is:", "", "| Company | Role |", "|---|---|"]
        out += [f"| {i.company} | {i.title} |" for i in rep.duplicate]
    return "\n".join(out) + "\n"
