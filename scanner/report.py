import random
from datetime import datetime
from pathlib import Path

from scanner.models import Job

STAGE_LABELS = {
    "raw": "raw records",
    "active_category": "active, visible, tech category",
    "fresh": "first seen within window",
    "location": "Canadian location",
    "company": "not on blocklist",
    "language": "not French-only",
    "title_match": "title matches tech roles",
    "title_not_senior": "not senior / non-software",
    "not_student_only": "not student-only",
    "weak_title": "  of which weak_title",
    "before dedupe": "before dedupe",
    "merged_url": "  merged by canonical URL",
    "merged_fuzzy": "  merged by fuzzy company+title",
}


def _cell(s: str) -> str:
    return (s or "").replace("|", "\\|").replace("\n", " ")


def format_counts(name: str, stages: list[tuple[str, int]]) -> str:
    lines = [f"{name}:"]
    lines += [f"  {STAGE_LABELS.get(k, k):<34}{v:>10,}" for k, v in stages]
    return "\n".join(lines)


def sample_rows(jobs: list[Job], n: int, seed: int | None = None) -> list[Job]:
    rng = random.Random(seed)
    return rng.sample(jobs, min(n, len(jobs)))


def format_sample(jobs: list[Job]) -> str:
    lines = []
    for j in jobs:
        fit = "n/a" if j.fit_pct is None else f"{j.fit_pct:g}%"
        lines.append(
            f"- {j.company} | {j.title} | {j.location} | {j.source_label()}\n"
            f"    Fit {fit} | Cluster {j.cluster or 'n/a'} | Score {j.score:g} | "
            f"Signals: {', '.join(j.signals) or '-'}\n"
            f"    Matched: {', '.join(j.matched[:8]) or '-'}\n"
            f"    Gaps: {', '.join(j.gaps[:8]) or '-'}")
    return "\n".join(lines)


def render(jobs: list[Job], since_days: float, now: datetime, meta: dict | None,
           stage_blocks: dict[str, list[tuple[str, int]]], total_deduped: int,
           phase2: dict | None = None) -> str:
    """`phase2` (optional): {"drops": Counter, "fetch": Counter, "fit_dist": [...], "held": int, "rescued": int}."""
    out = [
        "# Canadian entry-level tech jobs: candidates",
        "",
        f"- Generated: {now:%Y-%m-%d %H:%M} UTC",
        f"- Window: first seen in the last {since_days:g} day(s)",
        f"- Feashliaa dataset last updated: {meta['last_updated'] if meta else 'n/a'}",
        f"- Candidates after dedupe: **{total_deduped}**",
    ]
    if phase2:
        out.append(f"- Remaining after JD rules: **{len(jobs)}** (ranked by Score)")
    out += ["", "| First Seen | Company | Role | Location | Source | Tier | Fit % | Cluster | Signals | Gaps | URL |"
            if phase2 else "| First Seen | Company | Role | Location | Source | Tier | URL |",
            "|---|---|---|---|---|---|---|---|---|---|---|" if phase2 else "|---|---|---|---|---|---|---|"]
    for j in jobs:
        row = (f"| {j.first_seen:%Y-%m-%d} | {_cell(j.company)} | {_cell(j.title)} | "
               f"{_cell(j.location)} | {j.source_label()} | {'weak' if j.weak_title else 'strong'} | ")
        if phase2:
            fit = "n/a" if j.fit_pct is None else f"{j.fit_pct:g}"
            row += (f"{fit} | {j.cluster or ''} | {', '.join(j.signals)} | "
                    f"{_cell(', '.join(j.gaps[:6]))} | ")
        out.append(row + f"{j.url} |")
    out += ["", "## Stage counts", ""]
    for name, stages in stage_blocks.items():
        out += [f"**{name}**", "", "| Stage | Remaining |", "|---|---:|"]
        out += [f"| {STAGE_LABELS.get(k, k)} | {v:,} |" for k, v in stages]
        out.append("")
    if phase2:
        out += ["## JD rules", "", "**Drop reasons**", "", "| Reason | Jobs |", "|---|---:|"]
        out += [f"| {k} | {v} |" for k, v in sorted(phase2["drops"].items(), key=lambda kv: -kv[1])] or ["| none | 0 |"]
        out += ["", f"Student-titled jobs held for JD check: {phase2['held']}; rescued: {phase2['rescued']}.",
                "", "**JD fetch status** (source:status)", "", "| Source:status | Jobs |", "|---|---:|"]
        out += [f"| {k} | {v} |" for k, v in sorted(phase2["fetch"].items())]
        out += ["", "**Fit % distribution (kept jobs)**", "", "| Fit % | Jobs |", "|---|---:|"]
        out += [f"| {k} | {v} |" for k, v in phase2["fit_dist"]]
        out.append("")
    return "\n".join(out) + "\n"


def write(path: str | Path, text: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
