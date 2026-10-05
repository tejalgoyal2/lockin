"""Phase 1 pipeline: raw records → stage-counted filters → deduped Job list."""
from collections import Counter
from collections.abc import Iterable
from datetime import datetime, timedelta

from scanner.filters import Filters
from scanner.models import Job
from scanner.normalize import job_key


def run_stream(
    records: Iterable[dict],
    fields,
    filters: Filters,
    now: datetime,
    since_days: float,
    pre_stages: tuple = (),
) -> tuple[list[Job], list[tuple[str, int]]]:
    """Push each raw record through the stages in order.

    Returns surviving Jobs (not yet deduped) and ordered [(stage, count_after_stage)].
    A record is counted at stage N only if it passed every earlier stage.
    `pre_stages` are (name, predicate(raw_record)) pairs applied before normalisation.
    """
    cutoff = now - timedelta(days=since_days)
    names = ["raw", *[n for n, _ in pre_stages], "fresh", "location", "company",
             "language", "title_match", "title_not_senior", "not_student_only"]
    counts: Counter = Counter()
    kept: list[Job] = []

    for rec in records:
        counts["raw"] += 1
        if not _count_pre(rec, pre_stages, counts):
            continue
        f = fields(rec)
        if f["first_seen"] is None or f["first_seen"] < cutoff:
            continue
        counts["fresh"] += 1
        loc = filters.canadian_location(f["location"])
        if loc is None:
            continue
        counts["location"] += 1
        if not filters.company_ok(f["company"]):
            continue
        counts["company"] += 1
        if not filters.language_ok(f["title"]):
            continue
        counts["language"] += 1
        if not filters.title_included(f["title"]):
            continue
        counts["title_match"] += 1
        if not filters.title_not_excluded(f["title"]):
            continue
        counts["title_not_senior"] += 1
        if not filters.not_student_only(f["title"]):
            continue
        counts["not_student_only"] += 1
        kept.append(Job(
            company=f["company"], title=f["title"], location=loc, url=f["url"],
            source=f["source"], first_seen=f["first_seen"], sources={f["source"]},
            new_grad=f["source"] == "Simplify",
        ))
    return kept, [(n, counts[n]) for n in names]


def _count_pre(rec, pre_stages, counts) -> bool:
    """Count a record through the pre-stages; True only if it passes them all."""
    for name, pred in pre_stages:
        if not pred(rec):
            return False
        counts[name] += 1
    return True


def dedupe(jobs: Iterable[Job]) -> list[Job]:
    """Merge duplicates by Job Key (SPEC §7).

    Simplify contributes the new-grad flag; the Feashliaa/ATS row supplies the URL
    and source name, and the earliest first_seen wins.
    """
    merged: dict[str, Job] = {}
    for job in jobs:
        job.key = job_key(job.company, job.title, job.location)
        cur = merged.get(job.key)
        if cur is None:
            merged[job.key] = job
            continue
        cur.sources |= job.sources
        cur.new_grad = cur.new_grad or job.new_grad
        cur.first_seen = min(cur.first_seen, job.first_seen)
        if cur.source == "Simplify" and job.source != "Simplify":
            cur.source, cur.url = job.source, job.url
    return sorted(merged.values(), key=lambda j: j.first_seen, reverse=True)
