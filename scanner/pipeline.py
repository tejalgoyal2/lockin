"""Phase 1 pipeline: raw records → stage-counted filters → deduped Job list."""
from collections import Counter
from collections.abc import Iterable
from datetime import datetime, timedelta

from scanner.dedupe import dedupe  # noqa: F401  (re-exported)
from scanner.filters import Filters
from scanner.models import Job


def run_stream(
    records: Iterable[dict],
    fields,
    filters: Filters,
    now: datetime,
    since_days: float,
    pre_stages: tuple = (),
) -> tuple[list[Job], list[tuple[str, int]], list[Job]]:
    """Push each raw record through the stages in order.

    Returns (kept Jobs not yet deduped, ordered [(stage, count_after_stage)], student_held).
    A record is counted at stage N only if it passed every earlier stage.
    `pre_stages` are (name, predicate(raw_record)) pairs applied before normalisation.
    `student_held` are jobs that cleared every stage except the student-only title
    filter; phase 2 may rescue them from their JD text.
    """
    cutoff = now - timedelta(days=since_days)
    names = ["raw", *[n for n, _ in pre_stages], "fresh", "location", "company",
             "language", "title_match", "title_not_senior", "not_student_only"]
    counts: Counter = Counter()
    kept: list[Job] = []
    held: list[Job] = []

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
        tier = filters.title_tier(f["title"])
        if tier is None:
            continue
        counts["title_match"] += 1
        if not filters.title_not_excluded(f["title"]):
            continue
        counts["title_not_senior"] += 1
        job = Job(
            company=f["company"], title=f["title"], location=loc, url=f["url"],
            source=f["source"], first_seen=f["first_seen"], sources={f["source"]},
            new_grad=f["source"] == "Simplify", weak_title=tier == "weak",
        )
        if not filters.not_student_only(f["title"]):
            held.append(job)
            continue
        counts["not_student_only"] += 1
        if job.weak_title:
            counts["weak_title"] += 1
        kept.append(job)
    stages = [(n, counts[n]) for n in names]
    stages.append(("weak_title", counts["weak_title"]))
    return kept, stages, held


def _count_pre(rec, pre_stages, counts) -> bool:
    """Count a record through the pre-stages; True only if it passes them all."""
    for name, pred in pre_stages:
        if not pred(rec):
            return False
        counts[name] += 1
    return True
