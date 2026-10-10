"""Weekly gaps report (no Notion): which tech terms JDs ask for that skills.yaml lacks."""
from collections import defaultdict
from datetime import date, timedelta

from scanner.company_names import display_company
from scanner.models import Job
from scanner.score import Scorer
from scanner.state import GapsStore

TOP_N = 25
EXAMPLES = 3


def record_gaps(store: GapsStore, jobs: list[Job], scorer: Scorer, today: date,
                overrides: dict[str, str]) -> int:
    """Record gap terms for every job whose JD was fetched (kept or dropped), once per job key.

    Returns how many new jobs were recorded.
    """
    added = 0
    for job in jobs:
        if job.jd_status != "ok" or not job.key or job.location_dropped:   # not a Canadian job
            continue
        gaps = scorer.evaluate(job.jd).gaps
        if store.record(job.key, today, job.title, display_company(job, overrides), gaps):
            added += 1
    return added


def top_gaps(store: GapsStore, today: date, days: int, top_n: int = TOP_N) -> tuple[list[tuple[str, int, list[str]]], int]:
    """([(term, job_count, example titles)], jobs_in_window) for jobs recorded in the last `days` days."""
    cutoff = (today - timedelta(days=days)).isoformat()
    window = sorted(((e["date"], k, e) for k, e in store.jobs.items() if e["date"] > cutoff), reverse=True)
    counts: dict[str, int] = defaultdict(int)
    examples: dict[str, list[str]] = defaultdict(list)
    for _, _, entry in window:                       # newest first, so examples are recent
        for term in entry["gaps"]:
            counts[term] += 1
            if len(examples[term]) < EXAMPLES:
                examples[term].append(f"{entry['title']} ({entry['company']})")
    ranked = sorted(counts, key=lambda t: (-counts[t], t))[:top_n]
    return [(t, counts[t], examples[t]) for t in ranked], len(window)


def _table(rows: list[tuple[str, int, list[str]]]) -> list[str]:
    out = ["| Term | Jobs | Example titles |", "|---|---:|---|"]
    for term, n, ex in rows:
        out.append(f"| {term} | {n} | {'; '.join(e.replace('|', chr(92) + '|') for e in ex)} |")
    return out or ["_no gap terms recorded in this window_"]


def render(store: GapsStore, today: date) -> str:
    lines = [
        "# Gap terms: what JDs ask for that skills.yaml does not cover",
        "",
        f"Generated {today.isoformat()} (UTC). Counts are jobs, not mentions: each job is counted once, "
        "from every JD fetched (including jobs the filters later dropped). "
        "Move a term from `gaps.yaml` to `skills.yaml` once you have it.",
    ]
    for days in (7, 30):
        rows, n_jobs = top_gaps(store, today, days)
        lines += ["", f"## Top {TOP_N} gap terms, last {days} days ({n_jobs} jobs with a JD)", ""]
        lines += _table(rows) if rows else ["_no gap terms recorded in this window_"]
    return "\n".join(lines) + "\n"


def is_report_day(today: date) -> bool:
    """The report is regenerated on Mondays (UTC)."""
    return today.weekday() == 0
